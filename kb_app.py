from __future__ import annotations

import base64
import io
from pathlib import Path
import shutil
import tempfile

import streamlit as st
from PIL import Image, ImageChops

from kb_export import export_draft_to_docx_bytes, export_share_package_bytes, export_topic_bundle_zip_bytes, export_topic_document_bytes
from kb_parser import get_ocr_tool_status, parse_pdf
from kb_pipeline import DocumentAnalysis, KBDraft, KnowledgeMapNode, build_document_analysis, generate_kb_draft, split_draft_into_topic_documents
from kb_privacy import mask_sensitive_text
from kb_rag import answer_question as answer_document_question
from kb_store import (
    append_audit_event,
    authenticate_user,
    create_error_report,
    create_share_item,
    create_signup_user,
    export_error_reports_jsonl,
    export_feedback_jsonl,
    generate_share_code,
    get_settings,
    get_share_payload,
    init_db,
    list_audit_events,
    list_error_reports,
    list_feedback_items,
    list_share_items,
    list_users,
    reset_user_password,
    review_error_report,
    review_feedback_item,
    set_setting,
    submit_feedback,
    update_user_status,
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
THEME_PATH = BASE_DIR / "kb_theme.css"
LOGO_PATH = BASE_DIR / "image.png"
APP_VERSION = "0.2.0"

st.set_page_config(page_title="PDF2Knowledge AI", layout="wide")
init_db()

for key, default in {
    "parse_result": None,
    "draft": None,
    "auth_user": None,
    "auth_role": None,
    "auth_display_name": None,
    "selected_page": "home",
    "ui_language": "en",
    "kb_rag_result": None,
    "workspace_search": "",
    "last_uploaded_filename": "",
    "latest_share": None,
    "processing_status": "Ready",
    "last_error_id": None,
    "home_auth_mode": "signin",
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def t(en: str, ar: str) -> str:
    return ar if st.session_state.get("ui_language") == "ar" else en


def is_ar() -> bool:
    return st.session_state.get("ui_language") == "ar"


def apply_theme() -> None:
    css = THEME_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    lang = "ar" if is_ar() else "en"
    direction = "rtl" if is_ar() else "ltr"
    st.markdown(
        f"""
        <script>
        const root = window.parent.document.documentElement;
        root.setAttribute('lang', '{lang}');
        root.setAttribute('dir', '{direction}');
        </script>
        """,
        unsafe_allow_html=True,
    )


def current_settings() -> dict:
    return get_settings()


def current_role() -> str:
    return str(st.session_state.get("auth_role") or "")


def is_supervisor() -> bool:
    return current_role() == "supervisor"


def is_privileged_reader() -> bool:
    return current_role() in {"supervisor", "auditor"}


def secret_value(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    return str(value or default)


@st.cache_data(show_spinner=False)
def get_logo_data_uri() -> str:
    if not LOGO_PATH.exists():
        return ""
    image = Image.open(LOGO_PATH).convert("RGBA")
    bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
    diff = ImageChops.difference(image, bg)
    bbox = diff.getbbox()
    if bbox:
        image = image.crop(bbox)
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def hero_panel(parse_ready: bool, draft_ready: bool) -> None:
    step_states = [
        (t("Upload PDF", "رفع ملف PDF"), t("Collect one complex PDF and hand it to the local parser.", "تحميل ملف PDF معقد وتمريره إلى المحلل المحلي."), "done" if parse_ready else "active"),
        (t("AI Processing", "المعالجة بالذكاء الاصطناعي"), t("Detect sections, group topics, and build grounded KB drafts.", "اكتشاف الأقسام وتجميع الموضوعات وبناء مسودات معرفية موثقة."), "done" if draft_ready else ("active" if parse_ready else "pending")),
        (t("Generated Knowledge Articles", "المقالات المعرفية الناتجة"), t("Review topic cards and download editable Word documents.", "مراجعة بطاقات الموضوعات وتنزيل ملفات Word القابلة للتحرير."), "done" if draft_ready else "pending"),
    ]
    logo_markup = ""
    logo_uri = get_logo_data_uri()
    if logo_uri:
        logo_markup = f'<img class="kb-hero-logo" src="{logo_uri}" alt="PDF2Knowledge AI logo" />'
    st.markdown(
        f"""
        <div class="kb-shell">
          <section class="kb-hero kb-hero-grid">
            <div class="kb-hero-copy">
              <div class="kb-eyebrow">{t('Enterprise Knowledge Workflow', 'سير عمل المعرفة المؤسسية')}</div>
              <div class="kb-hero-brand">
                {logo_markup}
                <div>
                  <h1>{t('PDF2Knowledge AI', 'PDF2Knowledge AI')}</h1>
                  <p>{t('Transforming Complex PDFs into Structured Knowledge Articles.', 'تحويل ملفات PDF المعقدة إلى مقالات معرفية منظمة.')}</p>
                </div>
              </div>
              <div class="kb-hero-points">
                <span class="kb-chip">{t('Topic-based output', 'مخرجات مبنية على الموضوعات')}</span>
                <span class="kb-chip">{t('Editable Word export', 'تصدير Word قابل للتحرير')}</span>
                <span class="kb-chip">{t('Grounded retrieval', 'استرجاع موثق')}</span>
              </div>
            </div>
            <section class="kb-step-grid">
              {''.join(
                  f'<div class="kb-step-card"><span class="kb-step-state {state}">{t("Complete", "مكتمل") if state == "done" else t("In Progress", "قيد التنفيذ") if state == "active" else t("Pending", "قيد الانتظار")}</span><h3>{title}</h3><p>{body}</p></div>'
                  for title, body, state in step_states
              )}
            </section>
          </section>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_demo_analysis() -> DocumentAnalysis:
    return DocumentAnalysis(
        topics_detected=6,
        knowledge_articles_generated=5,
        confidence_score=92,
        policy_topics=[
            t("Password Policy", "سياسة كلمات المرور"),
            t("Data Protection", "حماية البيانات"),
        ],
        procedure_topics=[
            t("Incident Response", "الاستجابة للحوادث"),
            t("Backup Systems", "أنظمة النسخ الاحتياطي"),
        ],
        root_topic=t("Cybersecurity Policy", "سياسة الأمن السيبراني"),
        knowledge_map=KnowledgeMapNode(
            label=t("Cybersecurity Policy", "سياسة الأمن السيبراني"),
            children=[
                t("Network Security", "أمن الشبكات"),
                t("Password Policy", "سياسة كلمات المرور"),
                t("Incident Response", "الاستجابة للحوادث"),
                t("Data Protection", "حماية البيانات"),
            ],
        ),
    )


def render_page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="kb-page-header">
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_top_nav(status_label: str) -> None:
    nav_items = [
        ("home", t("Dashboard", "لوحة التحكم")),
        ("workspace", t("Process Document", "معالجة الوثيقة")),
        ("articles", t("Knowledge Articles", "المقالات المعرفية")),
    ]
    button_markup = "".join(
        f"<span class='kb-topnav-link {'active' if st.session_state.get('selected_page') == key else ''}'>{label}</span>"
        for key, label in nav_items
    )
    st.markdown(
        f"""
        <div class="kb-topnav">
          <div class="kb-topnav-brand">
            <span class="kb-topnav-title">{t('PDF2Knowledge AI', 'PDF2Knowledge AI')}</span>
            <span class="kb-topnav-links">{button_markup}</span>
          </div>
          <div class="kb-topnav-status">{t('Status', 'الحالة')}: {status_label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_home_nav(authenticated: bool) -> None:
    cta_label = t("Try Prototype", "جرّب النموذج")
    st.markdown(
        f"""
        <div class="kb-home-nav">
          <div class="kb-home-nav-left">
            <span class="kb-topnav-title">{t('PDF2Knowledge AI', 'PDF2Knowledge AI')}</span>
            <span class="kb-home-nav-links">
              <a class="kb-home-nav-link active" href="#home-top">{t('Home', 'الرئيسية')}</a>
              <a class="kb-home-nav-link" href="#how-it-works">{t('How It Works', 'كيف يعمل')}</a>
              <a class="kb-home-nav-link" href="#demo-output">{t('Demo', 'العرض')}</a>
              <a class="kb-home-nav-link" href="#about-impact">{t('About', 'حول')}</a>
            </span>
          </div>
          <div class="kb-home-nav-right">{cta_label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cta_cols = st.columns([4, 1.2])
    cta_cols[0].markdown(
        f"<div class='kb-home-nav-caption'>{t('AI product landing page for internal knowledge teams.', 'صفحة منتج بالذكاء الاصطناعي لفرق المعرفة الداخلية.')}</div>",
        unsafe_allow_html=True,
    )
    if cta_cols[1].button(cta_label, use_container_width=True, key="home_nav_cta"):
        st.session_state["selected_page"] = "workspace" if authenticated else "home"
        if not authenticated:
            st.session_state["home_auth_mode"] = "signin"
        st.rerun()


def render_home(authenticated: bool) -> None:
    draft_ready = st.session_state.get("draft") is not None
    render_home_nav(authenticated)
    demo_or_live = build_document_analysis(st.session_state["parse_result"], st.session_state["draft"]) if draft_ready else build_demo_analysis()

    logo_uri = get_logo_data_uri()
    workflow_diagram = f"""
    <div class="kb-flow-visual">
      <div class="kb-flow-node">PDF</div>
      <div class="kb-flow-arrow">→</div>
      <div class="kb-flow-node">{t('AI Analysis', 'تحليل الذكاء الاصطناعي')}</div>
      <div class="kb-flow-arrow">→</div>
      <div class="kb-flow-node">{t('Knowledge Articles', 'المقالات المعرفية')}</div>
    </div>
    """
    st.markdown(
        f"""
        <section class="kb-home-hero" id="home-top">
          <div class="kb-home-hero-copy">
            <div class="kb-eyebrow">{t('AI for Enterprise Knowledge Management', 'ذكاء اصطناعي لإدارة المعرفة المؤسسية')}</div>
            <h1>{t('Turn Complex PDFs into Structured Knowledge', 'حوّل ملفات PDF المعقدة إلى معرفة منظمة')}</h1>
            <p>{t('AI automatically analyzes large documents and converts them into organized knowledge articles ready for enterprise knowledge bases.', 'يقوم الذكاء الاصطناعي بتحليل الوثائق الكبيرة وتحويلها إلى مقالات معرفية منظمة جاهزة لقواعد المعرفة المؤسسية.')}</p>
            <div class="kb-home-cta-row">
              <span class="kb-chip">{t('Account sign-in supported', 'يدعم تسجيل الدخول بالحساب')}</span>
              <span class="kb-chip">{t('Supervisor approval for new accounts', 'موافقة المشرف للحسابات الجديدة')}</span>
            </div>
          </div>
          <div class="kb-home-hero-visual">
            {f'<img class="kb-home-hero-logo" src="{logo_uri}" alt="PDF2Knowledge AI logo" />' if logo_uri else ''}
            {workflow_diagram}
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    hero_cta_cols = st.columns([1, 1, 1.2])
    if authenticated:
        if hero_cta_cols[0].button(t("Open Workspace", "فتح مساحة العمل"), use_container_width=True):
            st.session_state["selected_page"] = "workspace"
            st.rerun()
        if hero_cta_cols[1].button(t("Open Articles", "فتح المقالات"), use_container_width=True):
            st.session_state["selected_page"] = "articles"
            st.rerun()
        if hero_cta_cols[2].button(t("Open Assistant", "فتح المساعد"), use_container_width=True):
            st.session_state["selected_page"] = "assistant"
            st.rerun()
    else:
        if hero_cta_cols[0].button(t("Upload PDF", "رفع PDF"), use_container_width=True):
            st.session_state["home_auth_mode"] = "signin"
        if hero_cta_cols[1].button(t("See Demo", "شاهد العرض"), use_container_width=True):
            st.session_state["home_auth_mode"] = "signin"
        hero_cta_cols[2].markdown(
            f"<div class='kb-note-card kb-cta-note'><p>{t('Protected internal access with sign-in and account approval.', 'وصول داخلي محمي عبر تسجيل الدخول وموافقة الحساب.')}</p></div>",
            unsafe_allow_html=True,
        )

    how_cards = [
        ("1", t("Upload Document", "رفع الوثيقة"), t("Upload a PDF containing complex documentation.", "ارفع ملف PDF يحتوي على وثائق معقدة.")),
        ("2", t("AI Analysis", "تحليل الذكاء الاصطناعي"), t("The system detects topics and extracts key knowledge.", "يكتشف النظام الموضوعات ويستخرج المعرفة الأساسية.")),
        ("3", t("Generate Articles", "إنشاء المقالات"), t("Structured Word documents are created for easy retrieval.", "يتم إنشاء ملفات Word منظمة لسهولة الاسترجاع.")),
    ]
    how_markup = "".join(
        f"<div class='kb-feature-card'><div class='kb-step-badge'>{step}</div><h3>{title}</h3><p>{body}</p></div>"
        for step, title, body in how_cards
    )
    st.markdown("<div id='how-it-works'></div>", unsafe_allow_html=True)
    st.markdown(f"### {t('How It Works', 'كيف يعمل')}")
    st.markdown(f"<div class='kb-feature-grid'>{how_markup}</div>", unsafe_allow_html=True)

    example_cards = [
        (t("Network Security", "أمن الشبكات"), t("Summary of network protection strategies.", "ملخص استراتيجيات حماية الشبكات."), ["security", "firewall", "monitoring"]),
        (t("Password Policy", "سياسة كلمات المرور"), t("Authentication rules and security guidelines.", "قواعد المصادقة وإرشادات الأمان."), ["authentication", "security"]),
    ]
    article_markup = "".join(
        f"""
        <div class='kb-topic-card'>
          <h3>{title}</h3>
          <p>{summary}</p>
          <div class='kb-topic-meta'>{''.join(f"<span class='kb-chip'>{tag}</span>" for tag in tags)}</div>
          <div class='kb-card-action'>{t('Download Word File', 'تنزيل ملف Word')}</div>
        </div>
        """
        for title, summary, tags in example_cards
    )
    st.markdown("<div id='demo-output'></div>", unsafe_allow_html=True)
    st.markdown(f"### {t('Generated Knowledge Articles', 'المقالات المعرفية الناتجة')}")
    st.markdown(f"<div class='kb-article-preview-grid'>{article_markup}</div>", unsafe_allow_html=True)

    st.markdown("<div id='about-impact'></div>", unsafe_allow_html=True)
    st.markdown(f"### {t('Enterprise Impact', 'الأثر المؤسسي')}")
    impact_cols = st.columns([1.05, 0.95], vertical_alignment="top")
    with impact_cols[0]:
        st.markdown(
            f"""
            <div class="kb-note-card">
              <h3>{t('Built for Enterprise Knowledge Management', 'مصمم لإدارة المعرفة المؤسسية')}</h3>
              <p>{t('Reduce manual document processing and prepare large PDFs for structured retrieval.', 'قلّل المعالجة اليدوية للوثائق وجهّز ملفات PDF الكبيرة للاسترجاع المنظم.')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(f"- {t('Reduce manual document processing', 'تقليل المعالجة اليدوية للوثائق')}")
        st.markdown(f"- {t('Improve information retrieval', 'تحسين استرجاع المعلومات')}")
        st.markdown(f"- {t('Enable AI-powered knowledge systems', 'تمكين أنظمة المعرفة المدعومة بالذكاء الاصطناعي')}")
        st.markdown(f"- {t('Support internal teams with account-based access and review flows', 'دعم الفرق الداخلية عبر الوصول بالحسابات وتدفقات المراجعة')}")
    with impact_cols[1]:
        metric_cols = st.columns(3)
        metric_cols[0].metric(t("Topics detected", "الموضوعات المكتشفة"), demo_or_live.topics_detected)
        metric_cols[1].metric(t("Knowledge articles generated", "المقالات المعرفية الناتجة"), demo_or_live.knowledge_articles_generated)
        metric_cols[2].metric(t("Confidence score", "درجة الثقة"), f"{demo_or_live.confidence_score}%")
        render_knowledge_map(demo_or_live, False)

    st.markdown(
        f"""
        <div class="kb-home-cta-block">
          <h3>{t('Start Transforming Your Documents Today', 'ابدأ في تحويل وثائقك اليوم')}</h3>
          <p>{t('Upload a PDF, let the AI process it, and retrieve structured knowledge articles in minutes.', 'ارفع ملف PDF، ودع الذكاء الاصطناعي يعالجه، واستخرج مقالات معرفية منظمة خلال دقائق.')}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    final_cta_cols = st.columns([1, 1.5])
    if authenticated:
        if final_cta_cols[0].button(t("Go to Workspace", "الانتقال إلى مساحة العمل"), use_container_width=True, key="final_cta_workspace"):
            st.session_state["selected_page"] = "workspace"
            st.rerun()
    else:
        if final_cta_cols[0].button(t("Upload a PDF", "رفع ملف PDF"), use_container_width=True, key="final_cta_upload"):
            st.session_state["home_auth_mode"] = "signin"
    final_cta_cols[1].markdown(
        f"<div class='kb-note-card'><p>{t('PDF2Knowledge AI • AI Hackathon Prototype • Built for the Malomatia AI Challenge', 'PDF2Knowledge AI • نموذج أولي للهاكاثون • مبني لتحدي مالوماتيا للذكاء الاصطناعي')}</p></div>",
        unsafe_allow_html=True,
    )

    if not authenticated:
        render_page_header(
            t("Access the Prototype", "الوصول إلى النموذج الأولي"),
            t("Internal access is account-based. Sign in with an approved account or create a new account for supervisor review.", "الوصول الداخلي يعتمد على الحسابات. سجّل الدخول بحساب معتمد أو أنشئ حساباً جديداً لمراجعة المشرف."),
        )
        access_cols = st.columns(2, vertical_alignment="top")
        with access_cols[0]:
            st.markdown(
                f"""
                <div class="kb-access-card">
                  <h3>{t('Sign In', 'تسجيل الدخول')}</h3>
                  <p>{t('Use an approved account to upload PDFs, process documents, and export knowledge articles.', 'استخدم حساباً معتمداً لرفع ملفات PDF ومعالجة الوثائق وتصدير المقالات المعرفية.')}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.form("login_form"):
                user_id = st.text_input(t("User ID", "معرف المستخدم"), value="")
                password = st.text_input(t("Password", "كلمة المرور"), type="password", value="")
                submitted = st.form_submit_button(t("Sign In", "تسجيل الدخول"), use_container_width=True)
            if submitted:
                user = authenticate_user(user_id, password)
                if user is None:
                    st.error(t("Invalid credentials or account not active.", "بيانات الدخول غير صحيحة أو الحساب غير نشط."))
                    append_audit_event(user_id or "anonymous", "login", "failure", {"reason": "invalid_or_inactive"})
                else:
                    st.session_state["auth_user"] = user["user_id"]
                    st.session_state["auth_role"] = user["role"]
                    st.session_state["auth_display_name"] = user["display_name"]
                    st.session_state["selected_page"] = "workspace"
                    append_audit_event(user["user_id"], "login", "success", {"role": user["role"]})
                    st.rerun()
            with st.expander(t("Demo accounts", "حسابات العرض")):
                st.write("kb_admin / Admin@123")
                st.write("kb_reviewer / Reviewer@123")
                st.write("kb_auditor / Auditor@123")
        with access_cols[1]:
            st.markdown(
                f"""
                <div class="kb-access-card">
                  <h3>{t('Create Account', 'إنشاء حساب')}</h3>
                  <p>{t('New accounts are submitted for supervisor approval before they can use the workspace.', 'يتم إرسال الحسابات الجديدة لموافقة المشرف قبل أن تتمكن من استخدام مساحة العمل.')}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.form("signup_form"):
                new_user_id = st.text_input(t("Requested user ID", "معرف المستخدم المطلوب"))
                new_display_name = st.text_input(t("Display name", "الاسم المعروض"))
                new_password = st.text_input(t("Password", "كلمة المرور"), type="password")
                signup = st.form_submit_button(t("Create Account", "إنشاء حساب"), use_container_width=True)
            if signup:
                ok, message = create_signup_user(new_user_id, new_display_name, new_password)
                if ok:
                    st.success(message)
                else:
                    st.error(message)


def logout() -> None:
    if st.session_state.get("auth_user"):
        append_audit_event(st.session_state["auth_user"], "logout", "success", {})
    st.session_state["auth_user"] = None
    st.session_state["auth_role"] = None
    st.session_state["auth_display_name"] = None
    st.session_state["parse_result"] = None
    st.session_state["draft"] = None
    st.session_state["latest_share"] = None
    st.session_state["home_auth_mode"] = "signin"
    st.session_state["selected_page"] = "home"
    st.rerun()


def sidebar_nav(settings: dict) -> str:
    with st.sidebar:
        logo_uri = get_logo_data_uri()
        st.markdown(
            f"""
            <div class="kb-sidebar-brand">
              {f'<img class="kb-sidebar-logo" src="{logo_uri}" alt="PDF2Knowledge AI logo" />' if logo_uri else ''}
              <div>
                <h2>{t('PDF2Knowledge AI', 'PDF2Knowledge AI')}</h2>
                <p>{t('Structured PDF decomposition for KB teams', 'تحليل PDF المنظم لفرق المعرفة')}</p>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        lang = st.radio(t("Language", "اللغة"), [("en", "English"), ("ar", "العربية")], format_func=lambda item: item[1], index=0 if not is_ar() else 1)
        st.session_state["ui_language"] = lang[0]
        st.divider()
        st.markdown(
            f"""
            <div class="kb-sidebar-card">
              <p><strong>{t('User', 'المستخدم')}:</strong> {st.session_state['auth_display_name']}</p>
              <p><strong>{t('Role', 'الدور')}:</strong> {st.session_state['auth_role']}</p>
              <p><strong>{t('Privacy', 'الخصوصية')}:</strong> {t('On', 'مفعل') if settings['privacy_masking_enabled'] else t('Off', 'معطل')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        nav_items = [
            ("home", t("Home", "الرئيسية")),
            ("workspace", t("Workspace", "مساحة العمل")),
            ("articles", t("Knowledge Articles", "المقالات المعرفية")),
            ("assistant", t("Ask Document", "اسأل الوثيقة")),
            ("settings", t("Settings", "الإعدادات")),
        ]
        if is_privileged_reader():
            nav_items.append(("audit", t("Audit", "التدقيق")))
        selected_page = st.session_state.get("selected_page", "home")
        if selected_page == "audit" and not is_privileged_reader():
            selected_page = "home"
        selected = st.radio(
            t("Navigation", "التنقل"),
            nav_items,
            format_func=lambda item: item[1],
            index=[item[0] for item in nav_items].index(selected_page),
        )
        st.session_state["selected_page"] = selected[0]
        if st.button(t("Sign Out", "تسجيل الخروج"), use_container_width=True):
            logout()
        return selected[0]


def render_topic_cards(draft: KBDraft, privacy_on: bool) -> None:
    topic_docs = split_draft_into_topic_documents(draft)
    st.markdown(f"### {t('Generated Knowledge Articles', 'المقالات المعرفية الناتجة')}")
    columns = st.columns(3)
    for index, topic in enumerate(topic_docs):
        with columns[index % 3]:
            title = mask_sensitive_text(topic.title, privacy_on)
            summary = mask_sensitive_text(topic.summary, privacy_on)
            st.markdown(
                f"""
                <div class='kb-topic-card'>
                  <div class='kb-topic-card-header'>
                    <div class='kb-topic-card-title'>{title}</div>
                    <div class='kb-topic-card-subtitle'>{t('Knowledge article', 'مقال معرفي')}</div>
                  </div>
                  <p class='kb-topic-card-summary'>{summary}</p>
                """,
                unsafe_allow_html=True,
            )
            if topic.tags:
                tag_markup = " ".join(f"<span class='kb-chip'>{mask_sensitive_text(tag, privacy_on)}</span>" for tag in topic.tags)
                st.markdown(f"<div class='kb-topic-meta'>{tag_markup}</div>", unsafe_allow_html=True)
            if topic.key_points:
                st.markdown(f"<div class='kb-topic-section-label'>{t('Key Points', 'النقاط الرئيسية')}</div>", unsafe_allow_html=True)
                for point in topic.key_points[:3]:
                    st.markdown(f"- {mask_sensitive_text(point, privacy_on)}")
            if topic.sections and topic.sections[0].source_pages:
                st.markdown(
                    f"<div class='kb-topic-meta'><span class='kb-chip'>{t('Pages', 'الصفحات')}: {', '.join(map(str, topic.sections[0].source_pages))}</span></div>",
                    unsafe_allow_html=True,
                )
            st.download_button(
                t("Download Word", "تنزيل Word"),
                data=export_topic_document_bytes(topic),
                file_name=f"{topic.topic_id}-{topic.title.lower().replace(' ', '-')[:50] or topic.topic_id}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"topic_dl_{topic.topic_id}",
                use_container_width=True,
            )
            st.markdown("</div>", unsafe_allow_html=True)


def render_knowledge_map(analysis: DocumentAnalysis, privacy_on: bool) -> None:
    st.markdown(f"### {t('Knowledge Map', 'خريطة المعرفة')}")
    root_label = mask_sensitive_text(analysis.knowledge_map.label, privacy_on)
    child_markup = "".join(
        f"<div class='kb-map-child'>{mask_sensitive_text(child, privacy_on)}</div>"
        for child in analysis.knowledge_map.children
    ) or f"<div class='kb-map-empty'>{t('No topic branches detected yet.', 'لم يتم اكتشاف فروع موضوعات بعد.')}</div>"
    st.markdown(
        f"""
        <div class="kb-map-shell">
          <div class="kb-map-root">{root_label}</div>
          <div class="kb-map-connector"></div>
          <div class="kb-map-children">
            {child_markup}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_active_document_state(settings: dict) -> tuple[object | None, KBDraft | None, bool, DocumentAnalysis | None]:
    parse_result = st.session_state.get("parse_result")
    draft: KBDraft | None = st.session_state.get("draft")
    privacy_on = bool(settings.get("privacy_masking_enabled", True))
    analysis = build_document_analysis(parse_result, draft) if parse_result is not None and draft is not None else None
    return parse_result, draft, privacy_on, analysis


def render_document_metrics(parse_result: object, analysis: DocumentAnalysis | None) -> None:
    metric_cols = st.columns(4)
    metric_cols[0].metric(t("Pages", "الصفحات"), len(parse_result.pages))
    metric_cols[1].metric(t("Detected Sections", "الأقسام المكتشفة"), len(parse_result.sections))
    metric_cols[2].metric(t("Table-like lines", "أسطر شبيهة بالجداول"), parse_result.total_tables)
    metric_cols[3].metric(t("Visual refs", "الإشارات البصرية"), parse_result.total_visual_references)
    if analysis is not None:
        analysis_cols = st.columns(3)
        analysis_cols[0].metric(t("Topics detected", "الموضوعات المكتشفة"), analysis.topics_detected)
        analysis_cols[1].metric(t("Knowledge articles generated", "المقالات المعرفية الناتجة"), analysis.knowledge_articles_generated)
        analysis_cols[2].metric(t("Confidence score", "درجة الثقة"), f"{analysis.confidence_score}%")
    status_cols = st.columns(3)
    status_cols[0].metric(t("OCR Pages", "صفحات OCR"), parse_result.ocr_pages)
    status_cols[1].metric(t("Images", "الصور"), parse_result.total_images)
    status_cols[2].metric(t("OCR Engine", "محرك OCR"), t("Ready", "جاهز") if parse_result.ocr_available else t("Unavailable", "غير متاح"))


def render_decomposition_sections(parse_result: object, privacy_on: bool, search_query: str) -> None:
    if parse_result.warnings:
        for warning in parse_result.warnings:
            st.warning(warning)
    visible_sections = []
    for section in parse_result.sections:
        haystack = f"{section.heading}\n{section.body}".lower()
        if not search_query or search_query in haystack:
            visible_sections.append(section)
    if not visible_sections:
        st.info(t("No detected sections matched the current search.", "لا توجد أقسام مطابقة لبحثك الحالي."))
        return
    for section in visible_sections:
        with st.expander(f"{section.heading} | {t('pages', 'الصفحات')} {', '.join(map(str, section.page_numbers))}"):
            st.write(mask_sensitive_text(section.body, privacy_on))
            if section.table_like_lines:
                st.markdown(f"**{t('Table-like lines', 'أسطر شبيهة بالجداول')}**")
                for line in section.table_like_lines:
                    st.code(mask_sensitive_text(line, privacy_on))
            if section.visual_references:
                st.markdown(f"**{t('Visual references', 'الإشارات البصرية')}**")
                for item in section.visual_references:
                    st.write(f"- {mask_sensitive_text(item, privacy_on)}")


def render_page_quality(parse_result: object, privacy_on: bool) -> None:
    for page in parse_result.pages:
        with st.expander(f"{t('Page', 'صفحة')} {page.page_number}"):
            st.text(mask_sensitive_text(page.text or "<no extractable text>", privacy_on))
            st.caption(
                f"{t('Quality', 'الجودة')}: {page.extraction_quality} | "
                f"{t('OCR used', 'تم استخدام OCR')}: {t('Yes', 'نعم') if page.ocr_used else t('No', 'لا')}"
            )


def render_scan_assist(parse_result: object) -> None:
    st.markdown(f"### {t('Scan Intake Guidance', 'إرشادات إدخال المسح')}")
    scan_pages = [page for page in parse_result.pages if page.likely_scanned]
    if scan_pages:
        st.warning(
            t(
                "Some pages look scan-heavy. Use OCR or a higher-contrast scan mode before final KB generation.",
                "بعض الصفحات تبدو معتمدة على المسح الضوئي. استخدم OCR أو وضع تباين أعلى قبل إنشاء نسخة المعرفة النهائية.",
            )
        )
    else:
        st.success(t("Current extraction quality looks usable for KB drafting.", "جودة الاستخراج الحالية تبدو مناسبة لصياغة مقالات المعرفة."))
    for page in parse_result.pages:
        with st.expander(f"{t('Page', 'صفحة')} {page.page_number}"):
            mode_label = {
                "original": t("Original mode", "الوضع الأصلي"),
                "grayscale": t("Grayscale / cleanup", "التدرج الرمادي / تحسين"),
                "ocr": t("OCR needed", "OCR مطلوب"),
            }.get(page.recommended_mode, page.recommended_mode)
            st.write(f"**{t('Extracted text characters', 'عدد أحرف النص المستخرج')}**: {page.text_char_count}")
            st.write(f"**{t('Embedded images', 'الصور المضمنة')}**: {page.image_count}")
            st.write(f"**{t('Recommended scan mode', 'وضع المسح الموصى به')}**: {mode_label}")
            st.write(f"**{t('Extraction quality', 'جودة الاستخراج')}**: {page.extraction_quality}")
            if page.likely_scanned:
                st.caption(t("This page likely came from an image-heavy scan and may benefit from OCR or mobile scan cleanup.", "هذه الصفحة تبدو معتمدة على مسح كثيف الصور وقد تستفيد من OCR أو تحسينات المسح عبر الهاتف."))
            if page.ocr_warning:
                st.caption(page.ocr_warning)


def render_review_draft_section(draft: KBDraft) -> None:
    draft.title = st.text_input(t("Draft title", "عنوان المسودة"), value=draft.title)
    draft.summary = st.text_area(t("Draft summary", "ملخص المسودة"), value=draft.summary, height=180)
    for idx, section in enumerate(draft.sections):
        st.markdown(f"### {t('Section', 'قسم')} {idx + 1}")
        section.heading = st.text_input(f"{t('Heading', 'العنوان')} {idx + 1}", value=section.heading, key=f"heading_{idx}")
        section.content = st.text_area(f"{t('Content', 'المحتوى')} {idx + 1}", value=section.content, height=180, key=f"content_{idx}")
        st.caption(f"{t('Source pages', 'الصفحات المصدرية')}: {', '.join(map(str, section.source_pages))}")
    st.session_state["draft"] = draft


def render_export_share_section(draft: KBDraft, privacy_on: bool) -> None:
    docx_bytes = export_draft_to_docx_bytes(draft)
    zip_bytes = export_topic_bundle_zip_bytes(draft)
    share_cols = st.columns([1.1, 0.9])
    with share_cols[0]:
        share_note = st.text_area(
            t("Share note for your team", "ملاحظة المشاركة للفريق"),
            value=t("Internal KB handoff package for review and retrieval onboarding.", "حزمة تسليم داخلية لمراجعة المعرفة وتجهيز الاسترجاع."),
            height=100,
            key="share_note",
        )
        if st.button(t("Create Share Package", "إنشاء حزمة مشاركة"), use_container_width=True):
            share_code = generate_share_code()
            payload = export_share_package_bytes(
                draft,
                share_code,
                share_note=share_note,
                source_filename=st.session_state.get("last_uploaded_filename", ""),
            )
            share_item = create_share_item(
                st.session_state["auth_user"],
                draft.title,
                share_note=share_note,
                source_filename=st.session_state.get("last_uploaded_filename", ""),
                payload_zip=payload,
                share_code=share_code,
            )
            st.session_state["latest_share"] = share_item
            st.rerun()
    with share_cols[1]:
        latest_share = st.session_state.get("latest_share")
        if latest_share:
            latest_payload = get_share_payload(latest_share["share_id"])
            st.markdown(
                f"""
                <div class="kb-note-card">
                  <h3>{t('Latest Share Package', 'أحدث حزمة مشاركة')}</h3>
                  <p><strong>{t('Share code', 'رمز المشاركة')}:</strong> {latest_share['share_code']}</p>
                  <p><strong>{t('Title', 'العنوان')}:</strong> {latest_share['title']}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.download_button(
                t("Download Share Package", "تنزيل حزمة المشاركة"),
                data=latest_payload or b"",
                file_name=f"share-{latest_share['share_code'].lower()}.zip",
                mime="application/zip",
                use_container_width=True,
                disabled=latest_payload is None,
            )
    action_cols = st.columns(2)
    action_cols[0].download_button(
        t("Download Master Word Draft", "تنزيل ملف Word الرئيسي"),
        data=docx_bytes,
        file_name="kb-draft.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True,
    )
    action_cols[1].download_button(
        t("Download Topic Bundle", "تنزيل حزمة الموضوعات"),
        data=zip_bytes,
        file_name="kb-topic-bundle.zip",
        mime="application/zip",
        use_container_width=True,
    )
    st.caption(t("The ZIP contains one master Word draft plus one topic-based Word file per detected topic.", "تحتوي الحزمة على ملف Word رئيسي واحد بالإضافة إلى ملف Word مستقل لكل موضوع مكتشف."))
    share_history = list_share_items(limit=20)
    if share_history:
        st.markdown(f"### {t('Recent Share History', 'سجل المشاركات الأخير')}")
        for item in share_history:
            with st.expander(f"{item['created_at_utc']} | {item['share_code']} | {item['title']}"):
                st.write(f"{t('Created by', 'أنشأها')}: {item['user_id']}")
                st.write(f"{t('Source file', 'الملف المصدر')}: {item['source_filename'] or '-'}")
                st.write(mask_sensitive_text(item["share_note"], privacy_on))
                item_payload = get_share_payload(item["share_id"])
                st.download_button(
                    t("Download Snapshot", "تنزيل اللقطة"),
                    data=item_payload or b"",
                    file_name=f"share-{item['share_code'].lower()}.zip",
                    mime="application/zip",
                    use_container_width=True,
                    disabled=item_payload is None,
                    key=f"share_dl_{item['share_id']}",
                )


def render_workspace(settings: dict) -> None:
    parse_result, draft, privacy_on, analysis = get_active_document_state(settings)
    render_top_nav(str(st.session_state.get("processing_status") or "Ready"))
    render_page_header(
        t("Process Document", "معالجة الوثيقة"),
        t("Upload a PDF, watch the AI workflow, inspect detected topics, and move into knowledge article generation.", "ارفع ملف PDF، وتابع سير عمل الذكاء الاصطناعي، وافحص الموضوعات المكتشفة، ثم انتقل إلى إنشاء المقالات المعرفية."),
    )
    dashboard_left, dashboard_right = st.columns([1.05, 0.95], vertical_alignment="top")
    with dashboard_left:
        st.markdown(f"### {t('Upload PDF', 'رفع ملف PDF')}")
        uploaded = st.file_uploader(t("Upload PDF", "رفع ملف PDF"), type=["pdf"])
        st.caption(t("Supported file type: PDF", "نوع الملف المدعوم: PDF"))
        st.caption(t("Recommended size: under 50MB", "الحجم الموصى به: أقل من 50 ميجابايت"))
        instruction = st.text_area(
            t("Guided instruction", "تعليمات موجهة"),
            value=t(
                "Create multiple knowledge-base articles for non-technical reviewers. Preserve facts, flag ambiguity, and keep each topic self-contained for retrieval.",
                "أنشئ عدة مقالات معرفية للمراجعين غير التقنيين. حافظ على الحقائق، وأبرز الغموض، واجعل كل موضوع مستقلاً وسهل الاسترجاع.",
            ),
            height=120,
        )
    with dashboard_right:
        st.markdown(f"### {t('Document Insights', 'رؤى الوثيقة')}")
        if analysis is not None:
            insight_cols = st.columns(2)
            insight_cols[0].metric(t("Pages analyzed", "الصفحات المحللة"), len(parse_result.pages) if parse_result else 0)
            insight_cols[1].metric(t("Topics detected", "الموضوعات المكتشفة"), analysis.topics_detected)
            insight_cols[0].metric(t("Articles generated", "المقالات الناتجة"), analysis.knowledge_articles_generated)
            insight_cols[1].metric(t("Confidence", "الثقة"), f"{analysis.confidence_score}%")
        elif parse_result is not None:
            insight_cols = st.columns(2)
            insight_cols[0].metric(t("Pages analyzed", "الصفحات المحللة"), len(parse_result.pages))
            insight_cols[1].metric(t("Detected sections", "الأقسام المكتشفة"), len(parse_result.sections))
        else:
            st.markdown(
                f"<div class='kb-note-card'><p>{t('Insights will appear after the document is processed.', 'ستظهر الرؤى بعد معالجة الوثيقة.')}</p></div>",
                unsafe_allow_html=True,
            )

        st.markdown(f"### {t('Topics Detected', 'الموضوعات المكتشفة')}")
        if analysis is not None and analysis.knowledge_map.children:
            for topic in analysis.knowledge_map.children:
                st.markdown(f"<div class='kb-topic-line'>{mask_sensitive_text(topic, privacy_on)}</div>", unsafe_allow_html=True)
        elif parse_result is not None:
            for section in parse_result.sections[:6]:
                page_range = f"{min(section.page_numbers)}-{max(section.page_numbers)}" if section.page_numbers else "-"
                st.markdown(
                    f"<div class='kb-topic-line'>{mask_sensitive_text(section.heading, privacy_on)} <span>{t('Pages', 'الصفحات')} {page_range}</span></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption(t("Topics will appear after processing.", "ستظهر الموضوعات بعد المعالجة."))

    lower_left, lower_right = st.columns([1.05, 0.95], vertical_alignment="top")
    with lower_left:
        st.markdown(f"### {t('Processing Document', 'معالجة الوثيقة')}")
        allow_openai = bool(settings.get("allow_openai_enhancement", True))
        secret_api_key = secret_value("openai_api_key", "")
        secret_model = secret_value("openai_model", "gpt-4o-mini")
        openai_api_key = st.text_input(t("OpenAI API key (optional)", "مفتاح OpenAI اختياري"), type="password", value="", placeholder=t("Uses deployed secret if left blank", "سيستخدم السر المنشور إذا تُرك فارغاً"), disabled=not allow_openai)
        openai_model = st.text_input(t("OpenAI model", "نموذج OpenAI"), value=secret_model, disabled=not allow_openai)
        effective_api_key = openai_api_key or secret_api_key or None
        if secret_api_key and not openai_api_key:
            st.caption(t("Deployed OpenAI secret is configured and will be used automatically.", "تم إعداد سر OpenAI المنشور وسيتم استخدامه تلقائياً."))

        process_clicked = st.button(
        t("Process", "معالجة"),
        disabled=uploaded is None,
        use_container_width=True,
        )
        current_status = str(st.session_state.get("processing_status") or "Ready")
        st.markdown(
            f"""
            <div class="kb-process-panel">
              <div class="kb-process-row {'done' if current_status in ['Extracting text', 'Detecting topics', 'Completed'] else 'active'}">✓ {t('PDF Uploaded', 'تم رفع PDF')}</div>
              <div class="kb-process-row {'done' if current_status in ['Detecting topics', 'Completed'] else 'active' if current_status == 'Extracting text' else 'pending'}">{'✓' if current_status in ['Detecting topics', 'Completed'] else '⟳' if current_status == 'Extracting text' else '•'} {t('Extracting Text', 'استخراج النص')}</div>
              <div class="kb-process-row {'done' if current_status == 'Completed' else 'active' if current_status == 'Detecting topics' else 'pending'}">{'✓' if current_status == 'Completed' else '⟳' if current_status == 'Detecting topics' else '•'} {t('Detecting Topics', 'اكتشاف الموضوعات')}</div>
              <div class="kb-process-row {'done' if current_status == 'Completed' else 'pending'}">{'✓' if current_status == 'Completed' else '•'} {t('Generating Knowledge Articles', 'إنشاء المقالات المعرفية')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with lower_right:
        render_page_header(
            t("Generated Knowledge Articles", "المقالات المعرفية الناتجة"),
            t("Review the generated article set and move directly into the full article workspace when ready.", "راجع مجموعة المقالات الناتجة وانتقل مباشرة إلى مساحة المقالات الكاملة عند الجاهزية."),
        )
        if draft is None:
            st.info(t("Process a document to see article cards here.", "قم بمعالجة وثيقة لعرض بطاقات المقالات هنا."))
        else:
            preview_draft = KBDraft(
                title=draft.title,
                summary=draft.summary,
                sections=draft.sections[:2],
                visual_notes=draft.visual_notes,
                table_notes=draft.table_notes,
                warnings=draft.warnings,
                llm_used=draft.llm_used,
            )
            render_topic_cards(preview_draft, privacy_on)
            if st.button(t("Open Full Knowledge Articles", "فتح المقالات المعرفية الكاملة"), use_container_width=True):
                st.session_state["selected_page"] = "articles"
                st.rerun()

    if process_clicked:
        if uploaded is None:
            st.warning(t("Upload a PDF first.", "قم برفع ملف PDF أولاً."))
            st.session_state["processing_status"] = "Ready"
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
                temp.write(uploaded.getvalue())
                temp_path = Path(temp.name)
            try:
                st.session_state["processing_status"] = "Extracting text"
                parse_result = parse_pdf(temp_path)
                st.session_state["processing_status"] = "Detecting topics"
                draft = generate_kb_draft(
                    parse_result,
                    instruction,
                    openai_api_key=effective_api_key,
                    openai_model=openai_model or "gpt-4o-mini",
                )
                st.session_state["parse_result"] = parse_result
                st.session_state["draft"] = draft
                st.session_state["kb_rag_result"] = None
                st.session_state["last_uploaded_filename"] = uploaded.name
                if settings.get("persist_uploaded_files"):
                    shutil.copyfile(temp_path, OUTPUT_DIR / uploaded.name)
                append_audit_event(st.session_state["auth_user"], "parse_pdf", "success", {"filename": uploaded.name})
                append_audit_event(st.session_state["auth_user"], "generate_draft", "success", {"llm_requested": bool(effective_api_key)})
                st.session_state["processing_status"] = "Completed"
                st.success(t("PDF processed and knowledge articles generated.", "تمت معالجة ملف PDF وإنشاء المقالات المعرفية."))
                st.rerun()
            except Exception as exc:
                append_audit_event(st.session_state["auth_user"], "generate_draft", "failure", {"error": str(exc)})
                error_id = create_error_report(
                    st.session_state["auth_user"],
                    "workspace_processing",
                    str(exc),
                    context={
                        "filename": uploaded.name,
                        "llm_requested": bool(effective_api_key),
                        "release_stage": settings.get("release_stage", "beta"),
                    },
                )
                st.session_state["last_error_id"] = error_id
                st.session_state["processing_status"] = "Failed"
                st.error(f"{t('Processing failed', 'فشلت المعالجة')}: {exc} ({t('Error ID', 'معرف الخطأ')} {error_id})")
            finally:
                temp_path.unlink(missing_ok=True)

    parse_result, draft, privacy_on, analysis = get_active_document_state(settings)
    if parse_result is None:
        st.info(t("Upload a PDF and click Process to begin the KB workflow.", "ارفع ملف PDF واضغط معالجة لبدء سير عمل المعرفة."))
        return
    render_document_metrics(parse_result, analysis)
    search_query = st.text_input(
        t("Search detected sections", "ابحث في الأقسام المكتشفة"),
        value=st.session_state.get("workspace_search", ""),
        key="workspace_search",
        placeholder=t("Search by heading or extracted content", "ابحث بالعنوان أو بالمحتوى المستخرج"),
    ).strip().lower()
    detail_tabs = st.tabs([t("Decomposition", "التفكيك"), t("Scan Assist", "مساعد المسح")])
    with detail_tabs[0]:
        render_decomposition_sections(parse_result, privacy_on, search_query)
    with detail_tabs[1]:
        render_scan_assist(parse_result)
    if draft is not None:
        nav_cols = st.columns(2)
        if nav_cols[0].button(t("Open Knowledge Articles", "فتح المقالات المعرفية"), use_container_width=True):
            st.session_state["selected_page"] = "articles"
            st.rerun()
        if nav_cols[1].button(t("Open Ask Document", "فتح اسأل الوثيقة"), use_container_width=True):
            st.session_state["selected_page"] = "assistant"
            st.rerun()
    last_error_id = st.session_state.get("last_error_id")
    if last_error_id:
        st.caption(f"{t('Latest captured error report', 'أحدث تقرير خطأ محفوظ')}: {last_error_id}")


def render_articles_page(settings: dict) -> None:
    parse_result, draft, privacy_on, _analysis = get_active_document_state(settings)
    render_page_header(
        t("Knowledge Articles", "المقالات المعرفية"),
        t("Review detected topics, edit the draft, and export Word outputs and share packages.", "راجع الموضوعات المكتشفة، وعدّل المسودة، وصدّر ملفات Word وحزم المشاركة."),
    )
    if parse_result is None or draft is None:
        st.info(t("Process a PDF first to generate topic-based articles.", "قم بمعالجة ملف PDF أولاً لإنشاء مقالات مبنية على الموضوعات."))
        return
    search_query = st.text_input(
        t("Search generated topics", "ابحث في الموضوعات الناتجة"),
        value="",
        key="articles_search",
        placeholder=t("Search by topic title or article content", "ابحث بعنوان الموضوع أو بمحتوى المقال"),
    ).strip().lower()
    topic_docs = split_draft_into_topic_documents(draft)
    summary_cols = st.columns(4)
    summary_cols[0].metric(t("Articles", "المقالات"), len(topic_docs))
    summary_cols[1].metric(t("Tagged topics", "موضوعات مع وسوم"), sum(1 for topic in topic_docs if topic.tags))
    summary_cols[2].metric(t("Sections in draft", "أقسام المسودة"), len(draft.sections))
    summary_cols[3].metric(t("Word outputs", "مخرجات Word"), len(topic_docs) + 1)
    tabs = st.tabs([
        t("Topic Cards", "بطاقات الموضوعات"),
        t("Generated Files", "الملفات الناتجة"),
        t("Review Draft", "مراجعة المسودة"),
        t("Export & Share", "التصدير والمشاركة"),
    ])
    with tabs[0]:
        if search_query:
            filtered = KBDraft(
                title=draft.title,
                summary=draft.summary,
                sections=[section for section in draft.sections if search_query in f"{section.heading}\n{section.content}".lower()],
                visual_notes=draft.visual_notes,
                table_notes=draft.table_notes,
                warnings=draft.warnings,
                llm_used=draft.llm_used,
            )
            if not filtered.sections:
                st.info(t("No generated topics matched the current search.", "لا توجد موضوعات مطابقة لبحثك الحالي."))
            else:
                render_topic_cards(filtered, privacy_on)
        else:
            render_topic_cards(draft, privacy_on)
    with tabs[1]:
        st.markdown(f"### {t('Generated Word Files', 'ملفات Word الناتجة')}")
        for topic in split_draft_into_topic_documents(draft):
            item_cols = st.columns([0.7, 0.3])
            item_cols[0].markdown(f"**{mask_sensitive_text(topic.title, privacy_on)}.docx**")
            item_cols[1].download_button(
                t("Download", "تنزيل"),
                data=export_topic_document_bytes(topic),
                file_name=f"{topic.topic_id}-{topic.title.lower().replace(' ', '-')[:50] or topic.topic_id}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"generated_file_dl_{topic.topic_id}",
                use_container_width=True,
            )
    with tabs[2]:
        render_review_draft_section(draft)
    with tabs[3]:
        render_export_share_section(draft, privacy_on)


def render_assistant_page(settings: dict) -> None:
    parse_result, draft, privacy_on, analysis = get_active_document_state(settings)
    render_page_header(
        t("Ask Document", "اسأل الوثيقة"),
        t("Query the uploaded PDF with grounded retrieval, citations, and a visible knowledge map.", "استعلم من ملف PDF المرفوع عبر استرجاع موثق مع الاستشهادات وخريطة معرفة مرئية."),
    )
    if parse_result is None:
        st.info(t("Process a PDF first to unlock grounded Q&A.", "قم بمعالجة ملف PDF أولاً لتفعيل الأسئلة والأجوبة الموثقة."))
        return
    left, right = st.columns([1.05, 0.95])
    with left:
        if analysis is None:
            st.info(t("Generate knowledge articles to see document analysis and the knowledge map.", "أنشئ المقالات المعرفية لعرض تحليل الوثيقة وخريطة المعرفة."))
        else:
            st.markdown(f"### {t('Document Analysis', 'تحليل الوثيقة')}")
            st.markdown(f"- **{t('Topics detected', 'الموضوعات المكتشفة')}**: {analysis.topics_detected}")
            st.markdown(f"- **{t('Knowledge articles generated', 'المقالات المعرفية الناتجة')}**: {analysis.knowledge_articles_generated}")
            st.markdown(f"- **{t('Confidence score', 'درجة الثقة')}**: {analysis.confidence_score}%")
            st.markdown(f"**{t('Policies detected', 'السياسات المكتشفة')}**")
            if analysis.policy_topics:
                for item in analysis.policy_topics:
                    st.markdown(f"- {mask_sensitive_text(item, privacy_on)}")
            else:
                st.caption(t("No policy-heavy topic was detected yet.", "لم يتم اكتشاف موضوعات سياسات واضحة بعد."))
            st.markdown(f"**{t('Procedures detected', 'الإجراءات المكتشفة')}**")
            if analysis.procedure_topics:
                for item in analysis.procedure_topics:
                    st.markdown(f"- {mask_sensitive_text(item, privacy_on)}")
            else:
                st.caption(t("No procedure-heavy topic was detected yet.", "لم يتم اكتشاف موضوعات إجراءات واضحة بعد."))
        st.markdown(f"### {t('Document RAG Assistant', 'مساعد الاسترجاع للوثيقة')}")
        question = st.text_area(
            t("Ask a grounded question about the uploaded PDF", "اطرح سؤالاً موثقاً حول ملف PDF المرفوع"),
            value=t("What are the main topics in this PDF?", "ما هي الموضوعات الرئيسية في ملف PDF هذا؟"),
            height=100,
            key="kb_rag_question",
        )
        secret_api_key = secret_value("openai_api_key", "")
        secret_model = secret_value("openai_model", "gpt-4o-mini")
        openai_api_key = secret_api_key
        openai_model = secret_model
        rag_cols = st.columns(3)
        top_k = rag_cols[0].selectbox("Top K", [3, 5, 7], index=1, key="kb_rag_topk")
        answer_language = rag_cols[1].selectbox(
            t("Answer language", "لغة الإجابة"),
            [("en", "English"), ("ar", "العربية")],
            format_func=lambda item: item[1],
            index=0 if not is_ar() else 1,
            key="kb_rag_language",
        )
        asked = rag_cols[2].button(t("Retrieve Answer", "استرجاع الإجابة"), use_container_width=True, key="kb_rag_ask")
        if asked:
            result = answer_document_question(
                parse_result,
                question,
                language=answer_language[0],
                top_k=int(top_k),
                openai_api_key=openai_api_key or None,
                openai_model=openai_model or "gpt-4o-mini",
            )
            st.session_state["kb_rag_result"] = result
            append_audit_event(
                st.session_state["auth_user"],
                "document_rag_query",
                "success",
                {"used_llm": bool(result.get("used_llm")), "top_k": int(top_k)},
            )
        rag_result = st.session_state.get("kb_rag_result")
        if rag_result:
            if rag_result.get("policy_blocked"):
                st.error(str(rag_result.get("answer")))
            else:
                if rag_result.get("insufficient_evidence"):
                    st.warning(str(rag_result.get("answer")))
                else:
                    st.markdown(mask_sensitive_text(str(rag_result.get("answer", "")), privacy_on))
    with right:
        if analysis is not None:
            render_knowledge_map(analysis, privacy_on)
        rag_result = st.session_state.get("kb_rag_result")
        if rag_result:
            hits = rag_result.get("hits") or []
            for hit in hits:
                with st.expander(f"{hit['topic_id']} / {hit['chunk_id']} - {hit['title']}"):
                    st.write(mask_sensitive_text(str(hit.get("text", "")), privacy_on))
                    st.caption(f"score={float(hit.get('rerank_score', 0.0)):.3f}")


def render_settings(settings: dict) -> None:
    render_page_header(
        t("Settings", "الإعدادات"),
        t("Personal account details, workspace controls, and supervisor support operations.", "تفاصيل الحساب الشخصية وضوابط مساحة العمل وعمليات الدعم للمشرف."),
    )
    feedback_items = list_feedback_items(limit=200)
    error_reports = list_error_reports(limit=200)
    open_count = sum(1 for item in feedback_items if item["status"] == "open")
    open_errors = sum(1 for item in error_reports if item["status"] == "open")
    ocr_status = get_ocr_tool_status()
    summary_cols = st.columns(6)
    summary_cols[0].metric(t("Version", "الإصدار"), APP_VERSION)
    summary_cols[1].metric(t("Release", "الإصدار التشغيلي"), str(settings.get("release_stage", "beta")).upper())
    summary_cols[2].metric(t("Privacy", "الخصوصية"), t("On", "مفعل") if settings.get("privacy_masking_enabled", True) else t("Off", "معطل"))
    summary_cols[3].metric(t("OCR", "OCR"), t("Ready", "جاهز") if ocr_status["available"] else t("Missing", "غير متاح"))
    summary_cols[4].metric(t("Open Feedback", "ملاحظات مفتوحة"), open_count)
    summary_cols[5].metric(t("Open Errors", "أخطاء مفتوحة"), open_errors)

    tabs = st.tabs([
        t("My Account", "حسابي"),
        t("Workspace Controls", "ضوابط مساحة العمل"),
        t("Administration", "الإدارة"),
        t("Support & Errors", "الدعم والأخطاء"),
    ])
    with tabs[0]:
        user_entry = next((item for item in list_users() if item["user_id"] == st.session_state["auth_user"]), None)
        st.markdown(
            f"""
            <div class="kb-setting-card">
              <h3>{st.session_state['auth_display_name']}</h3>
              <p><strong>{t('User ID', 'معرف المستخدم')}:</strong> {st.session_state['auth_user']}</p>
              <p><strong>{t('Role', 'الدور')}:</strong> {st.session_state['auth_role']}</p>
              <p><strong>{t('Account status', 'حالة الحساب')}:</strong> {user_entry['status'] if user_entry else '-'}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(f"- {t('Use Workspace to upload and process PDFs.', 'استخدم مساحة العمل لرفع ملفات PDF ومعالجتها.')}")
        st.markdown(f"- {t('Use Knowledge Articles to edit and export deliverables.', 'استخدم المقالات المعرفية لتحرير المخرجات وتصديرها.')}")
        st.markdown(f"- {t('Use Ask Document to inspect grounded answers and citations.', 'استخدم اسأل الوثيقة لفحص الإجابات الموثقة والاستشهادات.')}")
    with tabs[1]:
        can_edit_settings = is_supervisor()
        masking_enabled = st.toggle(t("Mask emails, phone numbers, and long IDs in the UI", "إخفاء البريد الإلكتروني وأرقام الهاتف والمعرفات الطويلة في الواجهة"), value=bool(settings.get("privacy_masking_enabled", True)), disabled=not can_edit_settings)
        allow_openai = st.toggle(t("Allow optional OpenAI draft enhancement", "السماح بتحسين المسودات عبر OpenAI بشكل اختياري"), value=bool(settings.get("allow_openai_enhancement", True)), disabled=not can_edit_settings)
        persist_files = st.toggle(t("Persist uploaded PDFs after parsing", "الاحتفاظ بملفات PDF المرفوعة بعد التحليل"), value=bool(settings.get("persist_uploaded_files", False)), disabled=not can_edit_settings)
        release_stage = st.selectbox(
            t("Release stage", "مرحلة الإصدار"),
            ["beta", "pilot", "internal"],
            index=["beta", "pilot", "internal"].index(str(settings.get("release_stage", "beta"))),
            disabled=not can_edit_settings,
        )
        st.markdown(
            f"<div class='kb-setting-card'><h3>{t('OCR Tool Status', 'حالة أداة OCR')}</h3><p>{ocr_status['message']}</p></div>",
            unsafe_allow_html=True,
        )
        if can_edit_settings and st.button(t("Save Settings", "حفظ الإعدادات"), use_container_width=True):
            set_setting("privacy_masking_enabled", masking_enabled)
            set_setting("allow_openai_enhancement", allow_openai)
            set_setting("persist_uploaded_files", persist_files)
            set_setting("release_stage", release_stage)
            append_audit_event(st.session_state["auth_user"], "save_settings", "success", {"privacy_masking_enabled": masking_enabled, "allow_openai_enhancement": allow_openai, "persist_uploaded_files": persist_files, "release_stage": release_stage})
            st.success(t("Settings saved.", "تم حفظ الإعدادات."))
            st.rerun()
        if not can_edit_settings:
            st.info(t("Workspace controls are supervisor-managed in this prototype.", "ضوابط مساحة العمل تدار من قبل المشرف في هذا النموذج الأولي."))
    with tabs[2]:
        if is_supervisor():
            st.markdown(
                f"<div class='kb-setting-card'><h3>{t('Collaboration Guidance', 'إرشادات التعاون')}</h3><p>{t('Invite teammates as GitHub collaborators and keep feature work on separate branches before merging.', 'قم بدعوة زملائك كمتعاونين على GitHub، واحتفظ بالعمل على الفروع المنفصلة قبل الدمج.')}</p></div>",
                unsafe_allow_html=True,
            )
            user_cards = st.columns(2)
            for idx, entry in enumerate(list_users()):
                with user_cards[idx % 2]:
                    st.markdown(
                        f"""
                        <div class="kb-setting-card kb-admin-user-card">
                          <h3>{entry['display_name']}</h3>
                          <p><strong>{t('User ID', 'معرف المستخدم')}:</strong> {entry['user_id']}</p>
                          <p><strong>{t('Role', 'الدور')}:</strong> {entry['role']}</p>
                          <p><strong>{t('Status', 'الحالة')}:</strong> {entry['status']}</p>
                          <p><strong>{t('Failed login attempts', 'محاولات الدخول الفاشلة')}:</strong> {entry['failed_login_attempts']}</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    action_cols = st.columns(3)
                    if action_cols[0].button(t("Activate", "تفعيل"), key=f"activate_{entry['user_id']}", use_container_width=True):
                        update_user_status(entry["user_id"], "active")
                        append_audit_event(st.session_state["auth_user"], "activate_user", "success", {"target_user": entry["user_id"]})
                        st.rerun()
                    if action_cols[1].button(t("Deactivate", "تعطيل"), key=f"deactivate_{entry['user_id']}", use_container_width=True):
                        update_user_status(entry["user_id"], "inactive")
                        append_audit_event(st.session_state["auth_user"], "deactivate_user", "success", {"target_user": entry["user_id"]})
                        st.rerun()
                    new_password = st.text_input(f"{t('Reset password', 'إعادة تعيين كلمة المرور')} • {entry['user_id']}", type="password", key=f"pwd_{entry['user_id']}")
                    if action_cols[2].button(t("Reset Password", "إعادة تعيين كلمة المرور"), key=f"reset_{entry['user_id']}", use_container_width=True) and new_password:
                        reset_user_password(entry["user_id"], new_password)
                        append_audit_event(st.session_state["auth_user"], "reset_password", "success", {"target_user": entry["user_id"]})
                        st.rerun()
        else:
            st.info(t("Administration is limited to supervisors.", "الإدارة متاحة للمشرفين فقط."))
    with tabs[3]:
        fb_left, fb_right = st.columns([1.2, 0.8])
        with fb_left:
            with st.form("feedback_form"):
                category = st.selectbox(
                    t("Category", "الفئة"),
                    [("general", t("General", "عام")), ("accuracy", t("Accuracy", "الدقة")), ("ui", t("UI", "الواجهة")), ("export", t("Export", "التصدير"))],
                    format_func=lambda item: item[1],
                )
                message = st.text_area(t("What should be improved?", "ما الذي يجب تحسينه؟"), height=120)
                send_feedback = st.form_submit_button(t("Submit Feedback", "إرسال الملاحظة"), use_container_width=True)
            if send_feedback:
                ok, msg = submit_feedback(st.session_state["auth_user"], category[0], message)
                if ok:
                    st.success(t("Feedback submitted.", "تم إرسال الملاحظة."))
                else:
                    st.error(msg)
        with fb_right:
            st.markdown(
                f"""
                <div class="kb-setting-card">
                  <h3>{t('System Status', 'حالة النظام')}</h3>
                  <p>{t('Open feedback items', 'ملاحظات مفتوحة')}: {open_count}</p>
                  <p>{t('Open error reports', 'تقارير الأخطاء المفتوحة')}: {open_errors}</p>
                  <p>{t('Audit visibility', 'وضوح التدقيق')}: {t('Enabled', 'مفعل')}</p>
                  <p>{t('Word export', 'تصدير Word')}: {t('Ready', 'جاهز')}</p>
                  <p>{t('Uploaded document search', 'البحث في الوثيقة')}: {t('Ready', 'جاهز')}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if is_privileged_reader():
                st.download_button(
                    t("Export Feedback JSONL", "تصدير الملاحظات JSONL"),
                    data=export_feedback_jsonl(),
                    file_name="feedback-export.jsonl",
                    mime="application/json",
                    use_container_width=True,
                )
                st.download_button(
                    t("Export Error Reports JSONL", "تصدير تقارير الأخطاء JSONL"),
                    data=export_error_reports_jsonl(),
                    file_name="error-reports.jsonl",
                    mime="application/json",
                    use_container_width=True,
                )
            else:
                st.caption(t("Feedback export is limited to supervisors and auditors.", "تصدير الملاحظات متاح للمشرفين والمدققين فقط."))
        if is_supervisor():
            st.markdown(f"### {t('Support Inbox', 'صندوق ملاحظات الدعم')}")
            feedback_cols = st.columns(2)
            for idx, item in enumerate(list_feedback_items(limit=20)):
                with feedback_cols[idx % 2]:
                    st.markdown(
                        f"""
                        <div class="kb-setting-card">
                          <h3>{item['category']} • {item['status']}</h3>
                          <p><strong>{t('User', 'المستخدم')}:</strong> {item['user_id']}</p>
                          <p><strong>{t('Created', 'تم الإنشاء')}:</strong> {item['created_at_utc']}</p>
                          <p>{mask_sensitive_text(item['message'], bool(settings.get('privacy_masking_enabled', True)))}</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if item["status"] == "open":
                        cols = st.columns(2)
                        if cols[0].button(t("Resolve", "إغلاق"), key=f"resolve_{item['feedback_id']}", use_container_width=True):
                            review_feedback_item(item["feedback_id"], st.session_state["auth_user"], "resolved")
                            append_audit_event(st.session_state["auth_user"], "resolve_feedback", "success", {"feedback_id": item["feedback_id"]})
                            st.rerun()
                        if cols[1].button(t("Keep Open", "إبقاء مفتوح"), key=f"reopen_{item['feedback_id']}", use_container_width=True):
                            review_feedback_item(item["feedback_id"], st.session_state["auth_user"], "open")
                            append_audit_event(st.session_state["auth_user"], "review_feedback", "success", {"feedback_id": item["feedback_id"], "status": "open"})
                            st.rerun()
            st.markdown(f"### {t('Error Reporting Inbox', 'صندوق تقارير الأخطاء')}")
            error_cols = st.columns(2)
            for idx, item in enumerate(list_error_reports(limit=20)):
                with error_cols[idx % 2]:
                    st.markdown(
                        f"""
                        <div class="kb-setting-card">
                          <h3>{item['source']} • {item['status']}</h3>
                          <p><strong>{t('User', 'المستخدم')}:</strong> {item['user_id']}</p>
                          <p><strong>{t('Created', 'تم الإنشاء')}:</strong> {item['created_at_utc']}</p>
                          <p>{mask_sensitive_text(item['message'], bool(settings.get('privacy_masking_enabled', True)))}</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if item["context"]:
                        st.json(item["context"])
                    if item["status"] == "open":
                        cols = st.columns(2)
                        if cols[0].button(t("Resolve Error", "إغلاق الخطأ"), key=f"resolve_error_{item['error_id']}", use_container_width=True):
                            review_error_report(item["error_id"], st.session_state["auth_user"], "resolved")
                            append_audit_event(st.session_state["auth_user"], "resolve_error_report", "success", {"error_id": item["error_id"]})
                            st.rerun()
                        if cols[1].button(t("Keep Open", "إبقاء مفتوح"), key=f"reopen_error_{item['error_id']}", use_container_width=True):
                            review_error_report(item["error_id"], st.session_state["auth_user"], "open")
                            append_audit_event(st.session_state["auth_user"], "review_error_report", "success", {"error_id": item["error_id"], "status": "open"})
                            st.rerun()


def render_audit() -> None:
    if not is_privileged_reader():
        st.error(t("Audit access is limited to supervisors and auditors.", "الوصول إلى التدقيق متاح للمشرفين والمدققين فقط."))
        return
    render_page_header(
        t("Audit", "التدقيق"),
        t("Recent account, processing, export, and settings events for the KB workspace.", "أحدث أحداث الحسابات والمعالجة والتصدير والإعدادات لمساحة عمل المعرفة."),
    )
    events = list_audit_events()
    if not events:
        st.info(t("No audit events yet.", "لا توجد أحداث تدقيق بعد."))
        return
    for item in events:
        with st.expander(f"{item['timestamp_utc']} | {item['action']} | {item['result']}"):
            st.write(f"{t('User', 'المستخدم')}: {item['user_id']}")
            st.json(item["details"])


apply_theme()
if not st.session_state.get("auth_user"):
    render_home(False)
else:
    settings = current_settings()
    selected = sidebar_nav(settings)
    if selected == "home":
        render_home(True)
    elif selected == "workspace":
        render_workspace(settings)
    elif selected == "articles":
        render_articles_page(settings)
    elif selected == "assistant":
        render_assistant_page(settings)
    elif selected == "settings":
        render_settings(settings)
    else:
        render_audit()
