"""
排程曆 — Streamlit Cloud 入口

把 index.html 原封不動嵌進頁面。行事曆本身是純前端，所有運算在瀏覽器完成，
資料存在瀏覽器本機，不會經過伺服器。
"""

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

HERE = Path(__file__).parent
PAGE = HERE / "index.html"

st.set_page_config(
    page_title="排程曆",
    page_icon="🗓️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 收掉 Streamlit 的外框留白，讓行事曆佔滿版面
st.markdown(
    """
    <style>
      [data-testid="stAppViewBlockContainer"]{padding:0 !important; max-width:none !important}
      [data-testid="stHeader"]{height:0; background:transparent}
      footer{display:none}
      [data-testid="stMainBlockContainer"]{padding:0 !important}
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    html = PAGE.read_text(encoding="utf-8")
except OSError:
    st.error(
        "找不到 index.html。這個頁面需要和 index.html 放在同一個資料夾，"
        "請確認 repo 裡兩個檔案都在。"
    )
    st.stop()

components.html(html, height=1500, scrolling=True)

with st.sidebar:
    st.markdown("### 排程曆")
    st.caption(
        "輸入截止日和需要的時數，系統自動排進週一到週五。"
        "所有資料存在你的瀏覽器裡，伺服器不會留存。"
    )
    st.markdown(
        "- 想長期保存，請用頁面右上角的 **匯出 → 下載 JSON** 備份\n"
        "- 想匯入 Google 日曆，用 **匯出 → 下載 .ics**\n"
        "- LINE 連動需要另外架 `line_bot.py`，步驟見 README"
    )
