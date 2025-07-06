# app.py

import streamlit as st
from datetime import datetime, timedelta
import time
import re
import os
import json
import pandas as pd
from dotenv import load_dotenv
from io import BytesIO # BytesIO는 여전히 필요 (Streamlit download_button의 data 인자)

# --- 모듈 임포트 ---
from modules import ai_service
from modules import database_manager
from modules import news_crawler
from modules import trend_analyzer
from modules import data_exporter # data_exporter 모듈 임포트


# --- Streamlit 앱 시작 ---
st.set_page_config(layout="wide", page_title="뉴스 트렌드 분석기")

st.title("📰 뉴스 트렌드 분석기")
st.markdown("원하는 키워드로 네이버 뉴스 트렌드를 감지하고, AI가 요약한 기사 내용을 확인하세요.")

# --- Potens.dev AI API 키 설정 ---
load_dotenv() # .env 파일 로드
POTENS_API_KEY = os.getenv("POTENS_API_KEY")

if not POTENS_API_KEY:
    st.error("🚨 오류: .env 파일에 'POTENS_API_KEY'가 설정되지 않았습니다. Potens.dev AI 기능을 사용할 수 없습니다.")
    st.stop() # API 키 없으면 앱 실행 중단

# 데이터베이스 초기화
database_manager.init_db()

# --- Streamlit Session State 초기화 (앱이 처음 로드될 때만 실행) ---
# 세션 상태가 초기화되지 않았다면 기본값 설정
if 'trending_keywords_data' not in st.session_state:
    st.session_state['trending_keywords_data'] = [] # 전체 트렌드 키워드 (내부 분석용)
if 'displayed_trending_keywords' not in st.session_state:
    st.session_state['displayed_keywords'] = [] # UI에 표시될 필터링된 트렌드 키워드 (이름 변경)
if 'final_collected_articles' not in st.session_state:
    st.session_state['final_collected_articles'] = [] # AI 요약된 최종 기사 목록
# submitted_flag는 폼 제출 시에만 True가 되도록 유지
if 'submitted_flag' not in st.session_state:
    st.session_state['submitted_flag'] = False
# analysis_completed 플래그는 분석 완료 시 True
if 'analysis_completed' not in st.session_state:
    st.session_state['analysis_completed'] = False
# DB 초기화 후 표시될 메시지
if 'db_status_message' not in st.session_state:
    st.session_state['db_status_message'] = ""
if 'db_status_type' not in st.session_state:
    st.session_state['db_status_type'] = ""


# --- UI 레이아웃: 검색 조건 (좌) & 키워드 트렌드 결과 (우) ---
col_search_input, col_trend_results = st.columns([1, 2]) # 1:2 비율로 컬럼 분할

with col_search_input:
    st.header("🔍 검색 조건 설정")
    with st.form("search_form"):
        keyword = st.text_input("검색할 뉴스 키워드 (예: '전기차')", value="전기차", key="keyword_input")
        total_search_days = st.number_input("총 몇 일간의 뉴스를 검색할까요? (예: 15)", min_value=1, value=15, key="total_days_input")
        recent_trend_days = st.number_input("최근 몇 일간의 데이터를 기준으로 트렌드를 분석할까요? (예: 2)", min_value=1, value=2, key="recent_days_input")
        max_naver_search_pages_per_day = st.number_input("각 날짜별로 네이버 뉴스 검색 결과 몇 페이지까지 크롤링할까요? (페이지당 10개 기사, 예: 3)", min_value=1, value=3, key="max_pages_input")

        # 폼 제출 버튼
        submitted = st.form_submit_button("뉴스 트렌드 분석 시작")

with col_trend_results:
    st.header("📈 키워드 트렌드 분석 결과")
    st.markdown("다음은 최근 언급량이 급증한 트렌드 키워드입니다.")

    # 이 컨테이너는 분석 진행 상황 메시지나 초기 메시지, 최종 결과를 표시합니다.
    # 표와 메시지를 분리하여 관리
    table_placeholder = st.empty() # 표를 표시할 컨테이너
    status_message_placeholder = st.empty() # 상태 메시지를 표시할 컨테이너

    # --- 분석 실행 및 결과 표시 (submitted 버튼 클릭 시) ---
    if submitted:
        # 새로운 검색 요청 시 기존 상태 초기화
        st.session_state['trending_keywords_data'] = []
        st.session_state['displayed_keywords'] = [] # 이름 변경 반영
        st.session_state['final_collected_articles'] = []
        st.session_state['submitted_flag'] = True
        st.session_state['analysis_completed'] = False
        st.session_state['db_status_message'] = ""
        st.session_state['db_status_type'] = ""

        # results_display_container를 비우고 새로운 진행 상황 표시
        table_placeholder.empty()
        my_bar = status_message_placeholder.progress(0, text="데이터 수집 및 분석 진행 중...")
        status_message_placeholder.info("네이버 뉴스 메타데이터 수집 중...")

        if recent_trend_days >= total_search_days:
            status_message_placeholder.error("오류: 최근 트렌드 분석 기간은 총 검색 기간보다 짧아야 합니다.")
        else:
            all_collected_news_metadata = []

            # 오늘 날짜 기준으로 검색 시작 날짜 계산
            today_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            search_start_date = today_date - timedelta(days=total_search_days - 1)

            # 프로그레스 바 계산을 위한 총 예상 기사 수 (페이지당 10개 기사 가정)
            # 정확한 기사 수는 크롤링 후에 알 수 있으므로, 최대 예상치로 진행바를 설정
            total_expected_articles = total_search_days * max_naver_search_pages_per_day * 10
            processed_article_count = 0


            for i in range(total_search_days):
                current_search_date = search_start_date + timedelta(days=i)
                formatted_search_date = current_search_date.strftime('%Y.%m.%d')

                daily_articles = news_crawler.crawl_naver_news_metadata(
                    keyword,
                    current_search_date,
                    max_naver_search_pages_per_day
                )

                for article in daily_articles:
                    processed_article_count += 1
                    # 프로그레스 바 업데이트: 실제 처리된 기사 수를 기준으로 진행률 계산
                    progress_percentage = processed_article_count / total_expected_articles
                    my_bar.progress(min(progress_percentage, 1.0), text=f"뉴스 메타데이터 수집 중... ({formatted_search_date}, {processed_article_count}개 기사 처리 완료)")


                    article_data_for_db = {
                        "제목": article["제목"],
                        "링크": article["링크"],
                        "날짜": article["날짜"].strftime('%Y-%m-%d'),
                        "내용": article["내용"]
                    }
                    database_manager.insert_article(article_data_for_db)

                    all_collected_news_metadata.append(article)

            my_bar.empty()
            status_message_placeholder.success(f"총 {len(all_collected_news_metadata)}개의 뉴스 메타데이터를 수집했습니다.")

            # --- 2. 키워드 트렌드 분석 실행 ---
            status_message_placeholder.info("키워드 트렌드 분석 중...")
            with st.spinner("키워드 트렌드 분석 중..."):
                trending_keywords_data = trend_analyzer.analyze_keyword_trends(
                    all_collected_news_metadata,
                    recent_days_period=recent_trend_days,
                    total_days_period=total_search_days
                )
            st.session_state['trending_keywords_data'] = trending_keywords_data

            if trending_keywords_data:
                # --- AI가 보험 개발자 관점에서 유의미한 키워드 선별 ---
                relevant_keywords_from_ai_raw = []
                with st.spinner("AI가 보험 개발자 관점에서 유의미한 키워드를 선별 중..."):
                    relevant_keywords_from_ai_raw = ai_service.get_relevant_keywords(
                        trending_keywords_data,
                        "차량보험사의 보험개발자",
                        POTENS_API_KEY
                    )

                filtered_trending_keywords = []
                if relevant_keywords_from_ai_raw:
                    filtered_trending_keywords = [
                        kw_data for kw_data in trending_keywords_data
                        if kw_data['keyword'] in relevant_keywords_from_ai_raw
                    ]
                    filtered_trending_keywords = sorted(filtered_trending_keywords, key=lambda x: x['recent_freq'], reverse=True)

                    status_message_placeholder.info(f"AI가 선별한 보험 개발자 관점의 유의미한 키워드 ({len(filtered_trending_keywords)}개): {[kw['keyword'] for kw in filtered_trending_keywords]}")
                else:
                    status_message_placeholder.warning("AI가 보험 개발자 관점에서 유의미한 키워드를 선별하지 못했습니다. 모든 트렌드 키워드를 표시합니다.")
                    filtered_trending_keywords = trending_keywords_data

                top_3_relevant_keywords = filtered_trending_keywords[:3]
                st.session_state['displayed_keywords'] = top_3_relevant_keywords # 이름 변경 반영

                if top_3_relevant_keywords:
                    pass
                else:
                    status_message_placeholder.info("보험 개발자 관점에서 유의미한 트렌드 키워드가 식별되지 않았습니다.")


                # --- 3. 트렌드 기사 본문 요약 (Potens.dev AI 활용) ---
                status_message_placeholder.info("트렌드 기사 본문 요약 중 (Potens.dev AI 호출)...")

                recent_trending_articles_candidates = [
                    article for article in all_collected_news_metadata
                    if article.get("날짜") and today_date - timedelta(days=recent_trend_days) <= article["날짜"]
                ]

                processed_links = set()

                articles_for_ai_summary = []
                for article in recent_trending_articles_candidates:
                    text_for_trend_check = article["제목"] + " " + article.get("내용", "")
                    article_keywords_for_trend = trend_analyzer.extract_keywords_from_text(text_for_trend_check)

                    if any(trend_kw['keyword'] in article_keywords_for_trend for trend_kw in top_3_relevant_keywords):
                        articles_for_ai_summary.append(article)

                total_ai_articles_to_process = len(articles_for_ai_summary)

                if total_ai_articles_to_process == 0:
                    status_message_placeholder.info("선별된 트렌드 키워드를 포함하는 최근 기사가 없거나, AI 요약 대상 기사가 없습니다.")
                else:
                    ai_progress_bar = st.progress(0, text=f"AI가 트렌드 기사를 요약 중... (0/{total_ai_articles_to_process} 완료)")
                    ai_processed_count = 0

                    temp_collected_articles = []
                    for article in articles_for_ai_summary:
                        if article["링크"] in processed_links:
                            continue

                        ai_processed_count += 1
                        ai_progress_bar.progress(ai_processed_count / total_ai_articles_to_process, text=f"AI가 트렌드 기사를 요약 중... ({ai_processed_count}/{total_ai_articles_to_process} 완료)")

                        article_date_str = article["날짜"].strftime('%Y-%m-%d') if article["날짜"] else 'N/A'

                        ai_processed_content = ai_service.get_article_summary(
                            article["제목"],
                            article["링크"],
                            article_date_str,
                            article["내용"],
                            POTENS_API_KEY,
                            max_attempts=2
                        )

                        final_content = ""
                        if ai_processed_content.startswith("Potens.dev AI 호출 최종 실패") or \
                           ai_processed_content.startswith("Potens.dev AI 호출에서 유효한 응답을 받지 못했습니다."):
                            final_content = f"본문 요약 실패 (AI 오류): {ai_processed_content}"
                            status_message_placeholder.error(f"AI 요약 실패: {final_content}")
                        else:
                            final_content = ai_service.clean_ai_response_text(ai_processed_content)

                        temp_collected_articles.append({
                            "제목": article["제목"],
                            "링크": article["링크"],
                            "날짜": article_date_str,
                            "내용": final_content
                        })
                        processed_links.add(article["링크"])
                        time.sleep(0.1)

                    ai_progress_bar.empty()
                    st.session_state['final_collected_articles'] = temp_collected_articles

                    if st.session_state['final_collected_articles']:
                        status_message_placeholder.success(f"총 {len(st.session_state['final_collected_articles'])}개의 트렌드 기사 요약을 완료했습니다.")
                    else:
                        status_message_placeholder.info("선별된 트렌드 키워드를 포함하는 기사가 없거나, AI 요약에 실패했습니다.")

            else:
                status_message_placeholder.info("선택된 기간 내에 식별된 트렌드 키워드가 없습니다.")

        st.session_state['submitted_flag'] = False
        st.session_state['analysis_completed'] = True

    if not st.session_state.get('submitted_flag', False) and \
       st.session_state.get('analysis_completed', False):
        if st.session_state['displayed_keywords']: # 이름 변경 반영
            df_top_keywords = pd.DataFrame(st.session_state['displayed_keywords']) # 이름 변경 반영
            df_top_keywords['surge_ratio'] = df_top_keywords['surge_ratio'].apply(
                lambda x: f"{x:.2f}x" if x != float('inf') else "새로운 트렌드"
            )
            table_placeholder.table(df_top_keywords)

            if st.session_state['final_collected_articles']:
                status_message_placeholder.success(f"총 {len(st.session_state['final_collected_articles'])}개의 트렌드 기사 요약을 완료했습니다.")
        else:
            status_message_placeholder.info("선택된 기간 내에 유의미한 트렌드 키워드가 식별되지 않았습니다.")
    elif not st.session_state.get('submitted_flag', False) and \
         not st.session_state.get('analysis_completed', False):
        empty_df = pd.DataFrame(columns=['keyword', 'recent_freq', 'past_freq', 'surge_ratio'])
        table_placeholder.table(empty_df)
        status_message_placeholder.info("검색 조건을 입력하고 '뉴스 트렌드 분석 시작' 버튼을 눌러주세요!")


# --- 데이터 다운로드 섹션 ---
st.header("📥 데이터 다운로드")
all_db_articles = database_manager.get_all_articles()

if all_db_articles:
    df_all_articles = pd.DataFrame(all_db_articles, columns=['제목', '링크', '날짜', '내용', '수집_시간'])
    df_all_articles['내용'] = df_all_articles['내용'].fillna('')

    # 모든 수집 뉴스 TXT 다운로드
    txt_data_all_crawled = data_exporter.export_articles_to_txt(
        [dict(zip(df_all_articles.columns, row)) for row in df_all_articles.values], # DataFrame을 dict list로 변환
        file_prefix="all_crawled_news"
    )

    # 모든 수집 뉴스 CSV 다운로드
    csv_data_all_crawled = data_exporter.export_articles_to_csv(df_all_articles)

    # 모든 수집 뉴스 XLSX 다운로드
    excel_data_all_crawled = data_exporter.export_articles_to_excel(df_all_articles, sheet_name='All_Crawled_News')


    # AI 요약된 기사 데이터 생성 (final_collected_articles)
    df_ai_summaries = pd.DataFrame(st.session_state['final_collected_articles'],
                                   columns=['제목', '링크', '날짜', '내용'])
    df_ai_summaries['내용'] = df_ai_summaries['내용'].fillna('')

    # AI 요약 TXT 다운로드
    txt_data_ai_summaries = data_exporter.export_articles_to_txt(
        [dict(zip(df_ai_summaries.columns, row)) for row in df_ai_summaries.values], # DataFrame을 dict list로 변환
        file_prefix="ai_summaries"
    )

    # AI 요약 XLSX 다운로드
    excel_data_ai_summaries = None
    if not df_ai_summaries.empty:
        excel_data_ai_summaries = data_exporter.export_articles_to_excel(df_ai_summaries, sheet_name='AI_Summaries')


    st.markdown("### 📊 수집된 전체 뉴스 데이터")
    col_all_data1, col_all_data2, col_all_data3 = st.columns(3)
    with col_all_data1:
        st.download_button(
            label="📄 TXT 다운로드",
            data=txt_data_all_crawled,
            file_name=data_exporter.generate_filename("all_crawled_news", "txt"),
            mime="text/plain",
            help="데이터베이스에 저장된 모든 뉴스를 텍스트 파일로 다운로드합니다."
        )
    with col_all_data2:
        st.download_button(
            label="📊 CSV 다운로드",
            data=csv_data_all_crawled.getvalue(), # BytesIO 객체에서 실제 바이트 값 가져오기
            file_name=data_exporter.generate_filename("all_crawled_news", "csv"),
            mime="text/csv",
            help="데이터베이스에 저장된 모든 뉴스를 CSV 파일로 다운로드합니다. (엑셀에서 깨질 경우 아래 안내 참조)"
        )
    with col_all_data3:
        st.download_button(
            label="엑셀 다운로드",
            data=excel_data_all_crawled.getvalue(), # BytesIO 객체에서 실제 바이트 값 가져오기
            file_name=data_exporter.generate_filename("all_crawled_news", "xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="데이터베이스에 저장된 모든 뉴스를 엑셀 파일(.xlsx)로 다운로드합니다. (한글 깨짐 없음)"
        )

    if not df_ai_summaries.empty:
        st.markdown("### 📝 AI 요약 기사")
        col_ai1, col_ai2 = st.columns(2)
        with col_ai1:
            st.download_button(
                label="📄 AI 요약 TXT 다운로드",
                data=txt_data_ai_summaries,
                file_name=data_exporter.generate_filename("ai_summaries", "txt"),
                mime="text/plain",
                help="AI가 요약한 트렌드 기사 내용을 텍스트 파일로 다운로드합니다."
            )
        with col_ai2:
            st.download_button(
                label="📊 AI 요약 엑셀 다운로드",
                data=excel_data_ai_summaries.getvalue(), # BytesIO 객체에서 실제 바이트 값 가져오기
                file_name=data_exporter.generate_filename("ai_summaries", "xlsx"),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                help="AI가 요약한 트렌드 기사 내용을 엑셀 파일(.xlsx)로 다운로드합니다."
            )
    else:
        st.info("AI 요약된 트렌드 기사가 없습니다. 먼저 분석을 실행하여 요약된 기사를 생성하세요.")


    st.markdown("---")
    col_db_info, col_db_clear = st.columns([2, 1])
    with col_db_info:
        st.info(f"현재 데이터베이스에 총 {len(all_db_articles)}개의 기사가 저장되어 있습니다.")
        if st.session_state['db_status_message']:
            if st.session_state['db_status_type'] == "success":
                st.success(st.session_state['db_status_message'])
            elif st.session_state['db_status_type'] == "error":
                st.error(st.session_state['db_status_message'])
            st.session_state['db_status_message'] = ""
            st.session_state['db_status_type'] = ""
        st.markdown("💡 **CSV 파일이 엑셀에서 깨질 경우:** 엑셀에서 '데이터' 탭 -> '텍스트/CSV 가져오기'를 클릭한 후, '원본 파일' 인코딩을 'UTF-8'로 선택하여 가져오세요.")
    with col_db_clear:
        if st.button("데이터베이스 초기화", help="데이터베이스의 모든 저장된 뉴스를 삭제합니다.", type="secondary"):
            database_manager.clear_db_content()
            st.session_state['trending_keywords_data'] = []
            st.session_state['displayed_keywords'] = [] # 이름 변경 반영
            st.session_state['final_collected_articles'] = []
            st.session_state['submitted_flag'] = False
            st.session_state['analysis_completed'] = False
            st.rerun()
