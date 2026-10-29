"""
تطبيق Streamlit لتحميل الفيديوهات (MP4 / MP3) وتلخيصها بالذكاء الاصطناعي (Gemini).

التشغيل:
    streamlit run app.py
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

import streamlit as st
import yt_dlp

# ----------------------------------------------------------------------------
# إعدادات الصفحة
# ----------------------------------------------------------------------------
st.set_page_config(page_title="محمّل ومُلخّص الفيديو", page_icon="🎬", layout="centered")

st.markdown(
    """
    <style>
        .stApp { direction: rtl; }
        .stMarkdown, .stTextInput label, .stSelectbox label, .stRadio label,
        .stAlert, h1, h2, h3 { text-align: right; }
        .stTextInput input { direction: ltr; text-align: left; }
    </style>
    """,
    unsafe_allow_html=True,
)

VIDEO_QUALITIES = {
    "أفضل جودة متاحة": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}

GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash"]

SUMMARY_LANGS = {"العربية": "Arabic", "English": "English", "Français": "French"}

SUMMARY_STYLES = {
    "ملخص قصير": "Write a short summary (one or two paragraphs).",
    "ملخص مفصل": (
        "Write a detailed summary organized with clear headings, "
        "followed by the key takeaways as bullet points."
    ),
    "نقاط رئيسية فقط": "Give only the main points as a concise bullet list.",
}


# ----------------------------------------------------------------------------
# دوال مساعدة
# ----------------------------------------------------------------------------
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def fmt_duration(seconds) -> str:
    if not seconds:
        return "غير معروفة"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


@st.cache_data(show_spinner=False, ttl=600)
def fetch_info(url: str) -> dict:
    """جلب معلومات الفيديو بدون تحميله."""
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title", "بدون عنوان"),
        "uploader": info.get("uploader") or info.get("channel") or "غير معروف",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
    }


def make_progress_hook(bar, status_box):
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            if total:
                pct = min(done / total, 1.0)
                speed = d.get("speed")
                speed_txt = f" - {speed / 1024 / 1024:.1f} MB/s" if speed else ""
                bar.progress(pct, text=f"جارٍ التحميل... {pct * 100:.0f}%{speed_txt}")
        elif d["status"] == "finished":
            bar.progress(1.0, text="اكتمل التحميل، جارٍ المعالجة والتحويل...")
            status_box.info("⚙️ جارٍ التحويل بواسطة ffmpeg...")

    return hook


def download_media(url: str, mode: str, outdir: str, height=None, audio_quality="192",
                   bar=None, status_box=None) -> Path:
    """
    تحميل الوسائط وإرجاع مسار الملف النهائي.
    mode: 'mp4' أو 'mp3'
    """
    opts = {
        "outtmpl": os.path.join(outdir, "%(title).80s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
        "retries": 3,
    }
    if bar is not None and status_box is not None:
        opts["progress_hooks"] = [make_progress_hook(bar, status_box)]

    if mode == "mp3":
        opts.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": audio_quality,
                    }
                ],
            }
        )
        ext = "mp3"
    else:
        sort = ["ext:mp4:m4a"]
        if height:
            sort.insert(0, f"res:{height}")
        opts.update(
            {
                "format": "bv*+ba/b",
                "format_sort": sort,
                "merge_output_format": "mp4",
            }
        )
        ext = "mp4"

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = sorted(Path(outdir).glob(f"*.{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError("لم يتم العثور على الملف الناتج بعد التحميل.")
    return files[0]


def summarize_audio(api_key: str, audio_path: Path, model: str, language: str, style: str) -> str:
    """رفع الملف الصوتي إلى Gemini وإرجاع الملخص."""
    from google import genai

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(file=str(audio_path))

    try:
        # انتظار انتهاء معالجة الملف على خوادم Google
        while uploaded.state and uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state and uploaded.state.name == "FAILED":
            raise RuntimeError("فشلت معالجة الملف الصوتي لدى Gemini.")

        prompt = (
            "You are given the audio track of a video. Listen carefully and summarize it.\n"
            f"{style}\n"
            f"Write the answer in {language}. "
            "If the audio contains no speech, say so briefly. "
            "Use Markdown formatting."
        )
        response = client.models.generate_content(model=model, contents=[uploaded, prompt])
        return response.text or "لم يُرجع النموذج أي نص."
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


# ----------------------------------------------------------------------------
# الواجهة
# ----------------------------------------------------------------------------
st.title("🎬 محمّل ومُلخّص الفيديو")
st.caption("حمّل الفيديو بصيغة MP4 أو MP3، أو لخّصه بالذكاء الاصطناعي (Gemini).")

if not ffmpeg_available():
    st.error(
        "⚠️ لم يتم العثور على **ffmpeg** على جهازك. هو مطلوب لتحويل MP3 ودمج الصوت والصورة. "
        "ثبّته ثم أعد تشغيل التطبيق."
    )

url = st.text_input("🔗 رابط الفيديو", placeholder="https://www.youtube.com/watch?v=...").strip()

if url:
    try:
        with st.spinner("جارٍ جلب معلومات الفيديو..."):
            info = fetch_info(url)
        col_img, col_txt = st.columns([1, 2])
        if info["thumbnail"]:
            col_img.image(info["thumbnail"], use_container_width=True)
        col_txt.markdown(
            f"**{info['title']}**\n\n"
            f"👤 {info['uploader']}\n\n"
            f"⏱️ {fmt_duration(info['duration'])}"
        )
    except Exception as e:
        st.warning(f"تعذّر جلب معلومات الفيديو، تأكد من الرابط. ({str(e)[:150]})")

tab_dl, tab_ai = st.tabs(["⬇️ تحميل", "🤖 تلخيص بالذكاء الاصطناعي"])

# ------------------------------- تبويب التحميل -------------------------------
with tab_dl:
    fmt_choice = st.radio("الصيغة", ["MP4 (فيديو)", "MP3 (صوت فقط)"], horizontal=True)
    is_mp4 = fmt_choice.startswith("MP4")

    if is_mp4:
        quality_label = st.selectbox("الجودة", list(VIDEO_QUALITIES.keys()))
    else:
        audio_q = st.selectbox("جودة الصوت (kbps)", ["320", "192", "128", "64"], index=1)

    if st.button("بدء التحميل", type="primary", disabled=not url, key="btn_download"):
        tmpdir = tempfile.mkdtemp(prefix="vdl_")
        bar = st.progress(0.0, text="جارٍ البدء...")
        status_box = st.empty()
        try:
            if is_mp4:
                path = download_media(url, "mp4", tmpdir, height=VIDEO_QUALITIES[quality_label],
                                      bar=bar, status_box=status_box)
                mime = "video/mp4"
            else:
                path = download_media(url, "mp3", tmpdir, audio_quality=audio_q,
                                      bar=bar, status_box=status_box)
                mime = "audio/mpeg"

            st.session_state["download"] = {
                "name": path.name,
                "data": path.read_bytes(),
                "mime": mime,
            }
            bar.empty()
            status_box.success("✅ تم التحميل بنجاح!")
        except Exception as e:
            bar.empty()
            status_box.error(f"❌ فشل التحميل: {str(e)[:300]}")
            st.session_state.pop("download", None)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if "download" in st.session_state:
        d = st.session_state["download"]
        size_mb = len(d["data"]) / 1024 / 1024
        st.download_button(
            f"💾 حفظ الملف ({size_mb:.1f} MB)",
            data=d["data"],
            file_name=d["name"],
            mime=d["mime"],
        )

# ------------------------------- تبويب التلخيص -------------------------------
with tab_ai:
    api_key = st.text_input(
        "🔑 مفتاح Gemini API",
        type="password",
        value=os.getenv("GEMINI_API_KEY", ""),
        help="احصل عليه مجاناً من https://aistudio.google.com/apikey",
    ).strip()

    c1, c2, c3 = st.columns(3)
    model = c1.selectbox("النموذج", GEMINI_MODELS)
    lang_label = c2.selectbox("لغة الملخص", list(SUMMARY_LANGS.keys()))
    style_label = c3.selectbox("نوع الملخص", list(SUMMARY_STYLES.keys()))

    if st.button("لخّص الفيديو", type="primary", disabled=not (url and api_key), key="btn_sum"):
        tmpdir = tempfile.mkdtemp(prefix="vsum_")
        try:
            with st.status("جارٍ العمل على الفيديو...", expanded=True) as status:
                st.write("1️⃣ استخراج الصوت من الفيديو...")
                bar = st.progress(0.0, text="جارٍ البدء...")
                box = st.empty()
                # جودة 64kbps كافية للكلام وتقلل حجم الملف المرفوع
                audio_path = download_media(url, "mp3", tmpdir, audio_quality="64",
                                            bar=bar, status_box=box)
                bar.empty()
                box.empty()

                size_mb = audio_path.stat().st_size / 1024 / 1024
                st.write(f"2️⃣ رفع الصوت ({size_mb:.1f} MB) وتحليله بواسطة Gemini...")
                summary = summarize_audio(
                    api_key, audio_path, model,
                    SUMMARY_LANGS[lang_label], SUMMARY_STYLES[style_label],
                )
                status.update(label="✅ اكتمل التلخيص", state="complete", expanded=False)
            st.session_state["summary"] = summary
        except Exception as e:
            st.error(f"❌ حدث خطأ: {str(e)[:400]}")
            st.session_state.pop("summary", None)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if "summary" in st.session_state:
        st.markdown("### 📝 الملخص")
        st.markdown(st.session_state["summary"])
        st.download_button(
            "💾 حفظ الملخص كملف نصي",
            data=st.session_state["summary"].encode("utf-8"),
            file_name="summary.txt",
            mime="text/plain",
        )

st.divider()
st.caption("⚖️ استخدم التطبيق فقط للمحتوى الذي يحق لك تحميله، والتزم بحقوق النشر وشروط استخدام المواقع.")