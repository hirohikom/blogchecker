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
st.caption("昨日〜今日にかけて更新されたブログを抽出し、GPT-4o-miniで要約します。")

# ローカル用にデフォルト値を設定
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

# --- キャッシュ対象のデータ取得・要約関数 ---
# api_key と target_date_str が変わらない限り、サーバーに保存されたキャッシュ（リスト）を返します
@st.cache_data(ttl=None)
def fetch_blog_data(api_key, target_date_str):
    client = OpenAI(api_key=api_key) if api_key else None
    
    # 文字列から日付オブジェクトを復元
    today = datetime.datetime.strptime(target_date_str, "%Y.%m.%d").date()
    yesterday = today - datetime.timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y.%m.%d")
    date_pattern = f"({re.escape(target_date_str)}|{re.escape(yesterday_str)})"
    
    base_url = "https://bokuao.com"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504], raise_on_status=False)
    session.mount("https://", HTTPAdapter(max_retries=retries))
    
    processed_urls = set()
    blog_results = []
    
    for page in [1, 2]:
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
                    title = data_lines[date_idx + 1] if date_idx < len(data_lines) - 1 else "（タイトルなし）"
                    
                    summary_text = "本文の取得に失敗しました。"
                    is_error = False
                    
                    try:
                        detail_res = session.get(href, headers=headers, timeout=10)
                        if detail_res.status_code == 200:
                            detail_soup = BeautifulSoup(detail_res.text, 'html.parser')
                            body_area = detail_soup.find('main') or detail_soup.find('article') or detail_soup.find('body')
                            
                            if body_area:
                                for element in body_area(["script", "style", "header", "footer", "nav"]):
                                    element.decompose()
                                
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
                                    
                                    time.sleep(0.5)
                                    
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
                                is_error = True
                        else:
                            is_error = True
                                    
                    except Exception as detail_err:
                        summary_text = f"通信エラー: {detail_err}"
                        is_error = True
                    
                    blog_results.append({
                        "href": href,
                        "member_name": member_name,
                        "matched_date": matched_date,
                        "title": title,
                        "summary_text": summary_text,
                        "is_error": is_error
                    })
            except Exception:
                continue
                
    return blog_results
# -----------------------------------------------

if st.session_state.authenticated:
    st.success("🔒 認証成功しました！")
    
    api_key = st.secrets.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        st.warning("⚠️ OPENAI_API_KEY が設定されていないため、簡易サマリーモードで動作します。")

    # 更新ボタン（キャッシュを強制的にクリアして再取得）
    if st.button("🔄 最新の情報に更新する"):
        st.cache_data.clear()
        st.rerun()
        
    # 日付を引数にしてキャッシュを制御 (日が変われば自動で新しいキャッシュが作られる)
    today_str = datetime.date.today().strftime("%Y.%m.%d")
    
    # 💡 ここを通った瞬間に自動でデータ取得（またはキャッシュ読み込み）が行われる
    with st.spinner("ブログ情報を取得・解析中です...（初回は少し時間がかかります）"):
        posts = fetch_blog_data(api_key, today_str)
        
    if not posts:
        st.info(f"対象期間のブログ更新は見つかりませんでした。")
    else:
        # 結果の表示
        for post in posts:
            with st.container():
                st.markdown(f"### 📝 {post['member_name']} のブログ")
                st.write(f"**投稿日:** {post['matched_date']}  |  **タイトル:** {post['title']}")
                
                if post['is_error']:
                    st.error(post['summary_text'])
                else:
                    st.info(post['summary_text'])
                    
                st.markdown(f"[👉 このブログを読む]({post['href']})")
                st.write("---")
                
        has_errors = any(p["is_error"] for p in posts)
        if has_errors:
            st.warning("一部のブログで要約エラーが発生しました。時間を置いてから「更新」ボタンを押してください。")