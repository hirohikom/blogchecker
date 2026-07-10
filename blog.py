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

if st.session_state.authenticated:
    st.success("🔒 認証成功しました！")
    
    # OpenAIのAPIキーを取得してクライアントを初期化
    api_key = st.secrets.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key) if api_key else None

    if not api_key:
        st.warning("⚠️ OPENAI_API_KEY が設定されていないため、簡易サマリーモードで動作します。")

    # 成功データを保持するキャッシュ
    if "blog_cache" not in st.session_state:
        st.session_state.blog_cache = {}
    if "run_check" not in st.session_state:
        st.session_state.run_check = False

    if st.button("🔄 ブログをチェック (新規取得)"):
        st.session_state.blog_cache = {}
        st.session_state.run_check = True

    if st.session_state.run_check:
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        
        today_str = today.strftime("%Y.%m.%d")
        yesterday_str = yesterday.strftime("%Y.%m.%d")
        date_pattern = f"({re.escape(today_str)}|{re.escape(yesterday_str)})"
        
        base_url = "https://bokuao.com"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        session = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504], raise_on_status=False)
        session.mount("https://", HTTPAdapter(max_retries=retries))
        
        has_update = False
        processed_urls = set()
        status_wrapper = st.empty()
        
        for page in [1, 2]:
            url = f"{base_url}/blog/list/1/0/" if page == 1 else f"{base_url}/blog/list/1/0/?writer=0&page={page}"
            if page > 1:
                time.sleep(0.5)
                
            try:
                status_wrapper.info(f"🔍 ページ {page} の一覧を解析中...")
                res = session.get(url, headers=headers, timeout=10)
                res.raise_for_status()
            except requests.RequestException as e:
                st.caption(f"⚠️ ページ {page} の取得でエラーが発生したためスキップしました: {e}")
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
                        if today_str in line:
                            date_idx = idx
                            matched_date = today_str
                            break
                        elif yesterday_str in line:
                            date_idx = idx
                            matched_date = yesterday_str
                            break
                    
                    if date_idx != -1:
                        member_name = data_lines[date_idx - 1] if date_idx > 0 else "メンバー不明"
                        title = data_lines[date_idx + 1] if date_idx < len(data_lines) - 1 else "（タイトルなし）"
                        has_update = True
                        
                        # キャッシュ判定
                        if href in st.session_state.blog_cache and not st.session_state.blog_cache[href]["is_error"]:
                            cached = st.session_state.blog_cache[href]
                            with st.container():
                                st.markdown(f"### 📝 {cached['member_name']} のブログ")
                                st.write(f"**投稿日:** {cached['matched_date']}  |  **タイトル:** {cached['title']}")
                                st.info(cached['summary_text'])
                                st.markdown(f"[👉 このブログを読む]({href})")
                                st.write("---")
                            continue

                        status_wrapper.info(f"📖 {member_name} さんのブログ本文を読み込み中...")
                        
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
                                            # f"2. 誰のブログかは既に明らかなため、最初の一文の冒頭に「〇〇（メンバー名）は〜」という主語は書かず、直接エピソードや感情から書き始めてください。\n\n"
                                            f"2. 誰のブログかは既に明らかなため、最初の一文の冒頭に「〇〇（メンバー名）は〜」という主語は書かないでください。\n"
                                            f"3. 「～します」というような主体者視点の表現ではなく、「～しています」というような客観視点の表現にしてください。\n\n"
                                            f"【本文】\n{clean_text}"
                                        )
                                        
                                        # API連続呼び出しのマナーとしての最低限のウェイト
                                        time.sleep(0.5)
                                        
                                        try:
                                            # OpenAI API (gpt-4o-mini) で要約を実行
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
                        
                        # 結果をキャッシュに保存
                        st.session_state.blog_cache[href] = {
                            "member_name": member_name,
                            "matched_date": matched_date,
                            "title": title,
                            "summary_text": summary_text,
                            "is_error": is_error
                        }
                        
                        # 画面へ出力
                        with st.container():
                            st.markdown(f"### 📝 {member_name} のブログ")
                            st.write(f"**投稿日:** {matched_date}  |  **タイトル:** {title}")
                            
                            if is_error:
                                st.error(summary_text)
                            else:
                                st.info(summary_text)
                                
                            st.markdown(f"[👉 このブログを読む]({href})")
                            st.write("---")
                except Exception:
                    continue
                    
        status_wrapper.empty()
                            
        if not has_update:
            st.info(f"対象期間 ({yesterday_str} 〜 {today_str}) のブログ更新は見つかりませんでした。")
        else:
            has_errors = any(v.get("is_error", False) for v in st.session_state.blog_cache.values())
            if has_errors:
                st.warning("一部のブログで要約エラーが発生しました。")
                if st.button("🔄 エラーになったブログだけ再試行する"):
                    st.rerun()