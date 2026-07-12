import datetime
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from bs4 import BeautifulSoup
import re
import streamlit as st
import time
from openai import OpenAI

st.title("☁️ 僕青 ブログ更新チェッカー (モバイル対応版)")
st.caption("最新1件のブログを抽出し、GPT-4o-miniで要約します。")

# --- メンバーリスト（絞り込み用） ---
ALL_MEMBERS = [
    "青木宙帆", "秋田莉杏", "安納蒼衣", "伊藤ゆず", "今井優希", 
    "岩本理瑚", "金澤亜美", "木下藍", "工藤唯愛", "塩釜菜那", 
    "杉浦英恋", "須永心海", "西森杏弥", "萩原心花", "長谷川稀未",
    "早﨑すずき", "宮腰友里亜", "八重樫美伊咲", "八木仁愛", "柳堀花怜", 
    "吉本此那",
]

# --- 認証処理 ---
APP_PASSWORD = st.secrets.get("APP_PASSWORD") or ["mysecret123"]

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    user_pass = st.text_input("認証パスワードを入力してください", type="password")
    if user_pass in APP_PASSWORD:
        st.session_state.authenticated = True
        st.rerun()
    elif user_pass:
        st.error("❌ パスワードが正しくありません。")
    st.info("🔓 正しいパスワードを入力するとチェッカーが起動します。")

# --- 1. ブログの「リスト」だけを取得してキャッシュする関数 ---
@st.cache_data(ttl=None)
@st.cache_data(ttl=None)
def fetch_blog_list(target_date_str):
    today = datetime.datetime.strptime(target_date_str, "%Y.%m.%d").date()
    yesterday = today - datetime.timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y.%m.%d")
    date_pattern = f"({re.escape(target_date_str)}|{re.escape(yesterday_str)})"
    
    base_url = "https://bokuao.com"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504], raise_on_status=False)
    session.mount("https://", HTTPAdapter(max_retries=retries))
    
    processed_urls = set()
    seen_members = set() # 🌟 追加：すでに処理したメンバーを記録するセット
    blog_list = []
    
    for page in range(1, 3): # ページ落ち対策で3ページまで探索
        url = f"{base_url}/blog/list/1/0/" if page == 1 else f"{base_url}/blog/list/1/0/?writer=0&page={page}"
        if page > 1:
            time.sleep(0.5)
            
        try:
            res = session.get(url, headers=headers, timeout=10)
            res.raise_for_status()
        except requests.RequestException:
            continue
            
        soup = BeautifulSoup(res.text, 'html.parser')
        date_tags = soup.find_all(string=re.compile(date_pattern))
        
        for date_tag in date_tags:
            card_container = date_tag.parent
            href = None
            
            for _ in range(3):
                if not card_container:
                    break
                a_tag = card_container.find('a', href=True) if card_container.name != 'a' else card_container
                if a_tag and '/blog/detail/' in a_tag['href']:
                    href = a_tag['href']
                    break
                card_container = card_container.parent

            if href and not href.startswith('http'):
                href = f"{base_url}{href}"
                
            if not href or href in processed_urls:
                continue
            processed_urls.add(href)
            
            container_text = card_container.get_text(separator="\n")
            lines = [line.strip() for line in container_text.split('\n') if line.strip()]
            data_lines = [l for l in lines if l != 'New']
            
            try:
                date_idx = -1
                matched_date = ""
                for idx, line in enumerate(data_lines):
                    if target_date_str in line:
                        date_idx = idx
                        matched_date = target_date_str
                        break
                    elif yesterday_str in line:
                        date_idx = idx
                        matched_date = yesterday_str
                        break
                
                if date_idx != -1:
                    member_name = data_lines[date_idx - 1] if date_idx > 0 else "メンバー不明"
                    
                    # 🌟 追加：このメンバーの「最新記事」をすでに拾っている場合はスキップ
                    if member_name in seen_members:
                        continue
                        
                    # まだ拾っていないメンバーならセットに追加してリストに登録
                    seen_members.add(member_name)
                    
                    title = data_lines[date_idx + 1] if date_idx < len(data_lines) - 1 else "（タイトルなし）"
                    
                    blog_list.append({
                        "href": href,
                        "member_name": member_name,
                        "matched_date": matched_date,
                        "title": title
                    })
            except Exception:
                continue
                
    return blog_list

# --- 2. ブログの「個別記事」を要約してキャッシュする関数 ---
@st.cache_data(ttl=None)
def fetch_blog_summary(href, api_key):
    client = OpenAI(api_key=api_key) if api_key else None
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    summary_text = "本文の取得に失敗しました。"
    is_error = False
    
    # WAF対策: 個別ページへのアクセス前に必ず1秒待機し、人間らしさを装う
    time.sleep(1.0)
    
    try:
        detail_res = requests.get(href, headers=headers, timeout=10)
        
        # HTTPエラー（403など）で弾かれた場合
        if detail_res.status_code != 200:
            return {
                "summary_text": f"アクセスが弾かれました (HTTP {detail_res.status_code})。連続アクセス制限の可能性があります。", 
                "is_error": True
            }
            
        detail_soup = BeautifulSoup(detail_res.text, 'html.parser')
        
        # --- 本文エリアの探索 ---
        # FC限定記事のスキップ処理により正規のページのみ取得できるため、大本命のタグのみをピンポイントで狙う
        body_area = detail_soup.find('div', class_='txt')
                
        if body_area:
            # 🌟 bodyまで落ちた時のために、ヘッダー・フッター・ナビゲーション・警告文を確実に消去
            # for element in body_area(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
            for element in body_area(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
                element.decompose()
            
            # # 念のため「JavaScriptが無効」などの定型文も要素ごと削除
            # for noise_text in body_area.find_all(string=re.compile("JavaScript|過去のブログは無料会員")):
            #     if noise_text.parent:
            #         noise_text.parent.decompose()
            
            raw_text = body_area.get_text(separator=" ")
            clean_text = re.sub(r'\s+', ' ', raw_text).strip()
            
            if client and len(clean_text) > 50:
                prompt = (
                    f"以下のアイドルブログの本文を読み、魅力を損なわない形で、3～5文程度の箇条書きで要約してください。\n\n"
                    f"【ルール】\n"
                    f"1. 語尾は必ず敬体（「〜ます」「〜です」など）で統一してください。\n"
                    f"2. 誰のブログかは既に明らかなため、最初の一文の冒頭に「〇〇（メンバー名）は〜」という主語は書かないでください。\n"
                    f"3. 「～します」というような主体者視点の表現ではなく、「～しています」というような客観視点の表現にしてください。\n\n"
                    f"【本文】\n{clean_text}"
                )
                
                try:
                    response = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": "あなたは優秀なアシスタントです。"},
                            {"role": "user", "content": prompt}
                        ],
                        timeout=15
                    )
                    summary_text = response.choices[0].message.content
                    is_error = False
                except Exception as openai_err:
                    summary_text = f"要約生成エラー: {openai_err}"
                    is_error = True
            else:
                summary_text = clean_text[:150] + "..." if len(clean_text) > 150 else clean_text
        else:
            # タグが見つからなかった場合（構造変更、文字なし写真のみ、会員限定など）
            summary_text = "本文タグが見つかりません。写真のみの記事、またはFC会員限定ページの可能性があります。"
            is_error = True
            
    except Exception as detail_err:
        summary_text = f"通信エラー: {detail_err}"
        is_error = True
        
    return {"summary_text": summary_text, "is_error": is_error}


# --- メイン処理 ---
if st.session_state.authenticated:
    st.success("🔒 認証成功しました！")
    
    api_key = st.secrets.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        st.warning("⚠️ OPENAI_API_KEY が設定されていないため、簡易サマリーモードで動作します。")

    # --- メンバー絞り込みUI (URLパラメータ連動) ---
    st.subheader("🔍 メンバーで絞り込む")
    saved_members = st.query_params.get_all("members")
    selected_members = st.multiselect(
        "表示したいメンバーを選択（未選択で全員表示）",
        options=ALL_MEMBERS,
        default=saved_members
    )
    
    # URLパラメータの更新
    if selected_members:
        st.query_params["members"] = selected_members
    else:
        if "members" in st.query_params:
            del st.query_params["members"]
    st.write("---")
        
    today_str = datetime.date.today().strftime("%Y.%m.%d")
    
    # 1. 一覧取得（爆速・全件取得）
    with st.spinner("ブログの一覧を取得しています..."):
        blog_list = fetch_blog_list(today_str)
        
    if not blog_list:
        st.info(f"対象期間のブログ更新は見つかりませんでした。")
    else:
        # 絞り込み処理
        if selected_members:
            filtered_blog_list = [p for p in blog_list if p['member_name'] in selected_members]
        else:
            filtered_blog_list = blog_list
            
        if not filtered_blog_list:
            st.info("選択されたメンバーのブログ更新はありませんでした。")
        
        # 2. 取得したリストをループで回し、順次要約・表示
        for post_meta in filtered_blog_list:
            with st.container():
                st.markdown(f"### 📝 {post_meta['member_name']} のブログ")
                st.write(f"**投稿日:** {post_meta['matched_date']}  |  **タイトル:** {post_meta['title']}")
                
                # 個別記事の要約を取得 (順次処理)
                summary_data = fetch_blog_summary(post_meta['href'], api_key)
                
                if summary_data['is_error']:
                    st.error(summary_data['summary_text'])
                else:
                    st.info(summary_data['summary_text'])
                    
                st.markdown(f"[👉 このブログを読む]({post_meta['href']})")
                st.write("---")
    
    # キャッシュクリアボタン
    if st.button("🔄 最新の情報に更新する"):
        st.cache_data.clear()
        st.rerun()
