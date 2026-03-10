from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

import streamlit as st

from kb_export import export_draft_to_docx_bytes, export_share_package_bytes, export_topic_bundle_zip_bytes, export_topic_document_bytes
from kb_parser import get_ocr_tool_status, parse_pdf
from kb_pipeline import DocumentAnalysis, KBDraft, build_document_analysis, generate_kb_draft, split_draft_into_topic_documents
from kb_privacy import mask_sensitive_text
from kb_rag import answer_question as answer_document_question
from kb_store import (
    append_audit_event,
    authenticate_user,
    create_share_item,
    create_signup_user,
    export_feedback_jsonl,
    generate_share_code,
    get_settings,
    get_share_payload,
    init_db,
    list_audit_events,
    list_feedback_items,
    list_share_items,
    list_users,
    reset_user_password,
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
    "selected_page": "workspace",
    "ui_language": "en",
    "kb_rag_result": None,
    "workspace_search": "",
    "last_uploaded_filename": "",
    "latest_share": None,
    "processing_status": "Ready",
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


def hero_panel(parse_ready: bool, draft_ready: bool) -> None:
    step_states = [
        (t("Upload PDF", "رفع ملف PDF"), t("Collect one complex PDF and hand it to the local parser.", "تحميل ملف PDF معقد وتمريره إلى المحلل المحلي."), "done" if parse_ready else "active"),
        (t("AI Processing", "المعالجة بالذكاء الاصطناعي"), t("Detect sections, group topics, and build grounded KB drafts.", "اكتشاف الأقسام وتجميع الموضوعات وبناء مسودات معرفية موثقة."), "done" if draft_ready else ("active" if parse_ready else "pending")),
        (t("Generated Knowledge Articles", "المقالات المعرفية الناتجة"), t("Review topic cards and download editable Word documents.", "مراجعة بطاقات الموضوعات وتنزيل ملفات Word القابلة للتحرير."), "done" if draft_ready else "pending"),
    ]
    hero_left, hero_right = st.columns([0.28, 0.72], vertical_alignment="center")
    with hero_left:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), use_container_width=True)
    with hero_right:
        st.markdown(
            f"""
            <div class="kb-shell">
              <section class="kb-hero">
                <div class="kb-eyebrow">{t('Enterprise Knowledge Workflow', 'سير عمل المعرفة المؤسسية')}</div>
                <h1>{t('PDF2Knowledge AI', 'PDF2Knowledge AI')}</h1>
                <p>{t('Transforming Complex PDFs into Structured Knowledge Articles.', 'تحويل ملفات PDF المعقدة إلى مقالات معرفية منظمة.')}</p>
              </section>
              <section class="kb-step-grid">
                {''.join(
                    f'<div class="kb-step-card"><span class="kb-step-state {state}">{t("Complete", "مكتمل") if state == "done" else t("In Progress", "قيد التنفيذ") if state == "active" else t("Pending", "قيد الانتظار")}</span><h3>{title}</h3><p>{body}</p></div>'
                    for title, body, state in step_states
                )}
              </section>
            </div>
            """,
            unsafe_allow_html=True,
        )


def login_screen() -> None:
    hero_panel(False, False)
    login_tab, signup_tab = st.tabs([t("Sign In", "تسجيل الدخول"), t("Create Account", "إنشاء حساب")])
    with login_tab:
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
                append_audit_event(user["user_id"], "login", "success", {"role": user["role"]})
                st.rerun()
        with st.expander(t("Demo accounts", "حسابات العرض")):
            st.write("kb_admin / Admin@123")
            st.write("kb_reviewer / Reviewer@123")
            st.write("kb_auditor / Auditor@123")
    with signup_tab:
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
    st.rerun()


def sidebar_nav(settings: dict) -> str:
    with st.sidebar:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), use_container_width=True)
        st.markdown(f"## {t('PDF2Knowledge AI', 'PDF2Knowledge AI')}")
        st.caption(t("Enterprise PDF decomposition for KB teams", "منصة مؤسسية لتحليل ملفات PDF لفرق المعرفة"))
        lang = st.radio(t("Language", "اللغة"), [("en", "English"), ("ar", "العربية")], format_func=lambda item: item[1], index=0 if not is_ar() else 1)
        st.session_state["ui_language"] = lang[0]
        st.divider()
        st.write(f"**{t('User', 'المستخدم')}:** {st.session_state['auth_display_name']}")
        st.write(f"**{t('Role', 'الدور')}:** {st.session_state['auth_role']}")
        st.write(f"**{t('Privacy Masking', 'إخفاء البيانات')}:** {t('On', 'مفعل') if settings['privacy_masking_enabled'] else t('Off', 'معطل')}")
        nav_items = [("workspace", t("Workspace", "مساحة العمل")), ("settings", t("Settings", "الإعدادات"))]
        if is_privileged_reader():
            nav_items.append(("audit", t("Audit", "التدقيق")))
        selected_page = st.session_state.get("selected_page", "workspace")
        if selected_page == "audit" and not is_privileged_reader():
            selected_page = "workspace"
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
            st.markdown("<div class='kb-topic-card'>", unsafe_allow_html=True)
            st.markdown(f"### {mask_sensitive_text(topic.title, privacy_on)}")
            st.markdown(mask_sensitive_text(topic.summary, privacy_on))
            if topic.tags:
                tag_markup = " ".join(f"<span class='kb-chip'>{mask_sensitive_text(tag, privacy_on)}</span>" for tag in topic.tags)
                st.markdown(f"<div class='kb-topic-meta'>{tag_markup}</div>", unsafe_allow_html=True)
            if topic.key_points:
                st.markdown(f"**{t('Key Points', 'النقاط الرئيسية')}**")
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


def render_workspace(settings: dict) -> None:
    parse_result = st.session_state.get("parse_result")
    draft: KBDraft | None = st.session_state.get("draft")
    privacy_on = bool(settings.get("privacy_masking_enabled", True))

    hero_panel(parse_result is not None, draft is not None)

    left, right = st.columns([1.25, 0.75])
    with left:
        uploaded = st.file_uploader(t("Upload PDF", "رفع ملف PDF"), type=["pdf"])
        instruction = st.text_area(
            t("Guided instruction", "تعليمات موجهة"),
            value=t(
                "Create multiple knowledge-base articles for non-technical reviewers. Preserve facts, flag ambiguity, and keep each topic self-contained for retrieval.",
                "أنشئ عدة مقالات معرفية للمراجعين غير التقنيين. حافظ على الحقائق، وأبرز الغموض، واجعل كل موضوع مستقلاً وسهل الاسترجاع.",
            ),
            height=120,
        )
    with right:
        allow_openai = bool(settings.get("allow_openai_enhancement", True))
        secret_api_key = secret_value("openai_api_key", "")
        secret_model = secret_value("openai_model", "gpt-4o-mini")
        openai_api_key = st.text_input(t("OpenAI API key (optional)", "مفتاح OpenAI اختياري"), type="password", value="", placeholder=t("Uses deployed secret if left blank", "سيستخدم السر المنشور إذا تُرك فارغاً"), disabled=not allow_openai)
        openai_model = st.text_input(t("OpenAI model", "نموذج OpenAI"), value=secret_model, disabled=not allow_openai)
        effective_api_key = openai_api_key or secret_api_key or None
        st.markdown(
            f"<div class='kb-note-card'><h3>{t('AI Process', 'مسار الذكاء الاصطناعي')}</h3><p>{t('The system parses the PDF locally, detects sections and topic signals, drafts retrieval-ready articles, and optionally uses OpenAI only to improve wording.', 'يقوم النظام بتحليل ملف PDF محلياً، ويكتشف الأقسام وإشارات الموضوعات، ثم يصيغ مقالات جاهزة للاسترجاع، ويستخدم OpenAI اختيارياً فقط لتحسين الصياغة.')}</p></div>",
            unsafe_allow_html=True,
        )
        if secret_api_key and not openai_api_key:
            st.caption(t("Deployed OpenAI secret is configured and will be used automatically.", "تم إعداد سر OpenAI المنشور وسيتم استخدامه تلقائياً."))

    process_cols = st.columns([1.1, 0.9])
    process_clicked = process_cols[0].button(
        t("Process", "معالجة"),
        disabled=uploaded is None,
        use_container_width=True,
    )
    process_cols[1].metric(t("Processing status", "حالة المعالجة"), str(st.session_state.get("processing_status") or "Ready"))

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
                st.session_state["processing_status"] = "Failed"
                st.error(f"{t('Processing failed', 'فشلت المعالجة')}: {exc}")
            finally:
                temp_path.unlink(missing_ok=True)

    parse_result = st.session_state.get("parse_result")
    draft = st.session_state.get("draft")
    if parse_result is None:
        return
    analysis = build_document_analysis(parse_result, draft) if draft is not None else None

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
    search_query = st.text_input(
        t("Search detected sections and generated topics", "ابحث في الأقسام المكتشفة والموضوعات الناتجة"),
        value=st.session_state.get("workspace_search", ""),
        key="workspace_search",
        placeholder=t("Search by heading, content, or topic", "ابحث بالعنوان أو المحتوى أو الموضوع"),
    ).strip().lower()

    tabs = st.tabs([
        t("Analysis", "التحليل"),
        t("Decomposition", "التفكيك"),
        t("Pages", "الصفحات"),
        t("Knowledge Articles", "المقالات المعرفية"),
        t("Ask Document", "اسأل الوثيقة"),
        t("Scan Assist", "مساعد المسح"),
        t("Review Draft", "مراجعة المسودة"),
        t("Export & Share", "التصدير والمشاركة"),
    ])

    with tabs[0]:
        if analysis is None:
            st.info(t("Generate knowledge articles to see document analysis and the knowledge map.", "أنشئ المقالات المعرفية لعرض تحليل الوثيقة وخريطة المعرفة."))
        else:
            left, right = st.columns([1.1, 0.9])
            with left:
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
            with right:
                render_knowledge_map(analysis, privacy_on)

    with tabs[1]:
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

    with tabs[2]:
        for page in parse_result.pages:
            with st.expander(f"{t('Page', 'صفحة')} {page.page_number}"):
                st.text(mask_sensitive_text(page.text or "<no extractable text>", privacy_on))
                st.caption(
                    f"{t('Quality', 'الجودة')}: {page.extraction_quality} | "
                    f"{t('OCR used', 'تم استخدام OCR')}: {t('Yes', 'نعم') if page.ocr_used else t('No', 'لا')}"
                )

    with tabs[3]:
        if draft is None:
            st.info(t("Generate knowledge articles to see the topic cards.", "أنشئ المقالات المعرفية لعرض بطاقات الموضوعات."))
        else:
            if search_query:
                filtered = KBDraft(
                    title=draft.title,
                    summary=draft.summary,
                    sections=[
                        section
                        for section in draft.sections
                        if search_query in f"{section.heading}\n{section.content}".lower()
                    ],
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

    with tabs[4]:
        st.markdown(f"### {t('Document RAG Assistant', 'مساعد الاسترجاع للوثيقة')}")
        question = st.text_area(
            t("Ask a grounded question about the uploaded PDF", "اطرح سؤالاً موثقاً حول ملف PDF المرفوع"),
            value=t("What are the main topics in this PDF?", "ما هي الموضوعات الرئيسية في ملف PDF هذا؟"),
            height=100,
            key="kb_rag_question",
        )
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
                openai_api_key=effective_api_key,
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
                hits = rag_result.get("hits") or []
                for hit in hits:
                    with st.expander(f"{hit['topic_id']} / {hit['chunk_id']} - {hit['title']}"):
                        st.write(mask_sensitive_text(str(hit.get("text", "")), privacy_on))
                        st.caption(f"score={float(hit.get('rerank_score', 0.0)):.3f}")

    with tabs[5]:
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

    with tabs[6]:
        if draft is None:
            st.info(t("Generate a draft to review and edit it here.", "أنشئ مسودة لمراجعتها وتحريرها هنا."))
        else:
            draft.title = st.text_input(t("Draft title", "عنوان المسودة"), value=draft.title)
            draft.summary = st.text_area(t("Draft summary", "ملخص المسودة"), value=draft.summary, height=180)
            for idx, section in enumerate(draft.sections):
                st.markdown(f"### {t('Section', 'قسم')} {idx + 1}")
                section.heading = st.text_input(f"{t('Heading', 'العنوان')} {idx + 1}", value=section.heading, key=f"heading_{idx}")
                section.content = st.text_area(f"{t('Content', 'المحتوى')} {idx + 1}", value=section.content, height=180, key=f"content_{idx}")
                st.caption(f"{t('Source pages', 'الصفحات المصدرية')}: {', '.join(map(str, section.source_pages))}")
            st.session_state["draft"] = draft

    with tabs[7]:
        if draft is None:
            st.info(t("Generate a draft first.", "أنشئ مسودة أولاً."))
        else:
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


def render_settings(settings: dict) -> None:
    st.markdown(f"## {t('Settings', 'الإعدادات')}")
    st.caption(t("Enterprise privacy, deployment, and account controls for the KB workspace.", "ضوابط الخصوصية والنشر والحسابات لمساحة عمل المعرفة."))
    status_cols = st.columns(4)
    status_cols[0].metric(t("Version", "الإصدار"), APP_VERSION)
    status_cols[1].metric(t("Release Stage", "مرحلة الإصدار"), str(settings.get("release_stage", "beta")).upper())
    status_cols[2].metric(t("RAG Assistant", "مساعد الاسترجاع"), t("Ready", "جاهز"))
    status_cols[3].metric(t("Privacy Masking", "إخفاء البيانات"), t("On", "مفعل") if settings.get("privacy_masking_enabled", True) else t("Off", "معطل"))
    left, right = st.columns(2)
    with left:
        st.subheader(t("Privacy & Security", "الخصوصية والأمان"))
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
        if can_edit_settings and st.button(t("Save Settings", "حفظ الإعدادات"), use_container_width=True):
            set_setting("privacy_masking_enabled", masking_enabled)
            set_setting("allow_openai_enhancement", allow_openai)
            set_setting("persist_uploaded_files", persist_files)
            set_setting("release_stage", release_stage)
            append_audit_event(st.session_state["auth_user"], "save_settings", "success", {"privacy_masking_enabled": masking_enabled, "allow_openai_enhancement": allow_openai, "persist_uploaded_files": persist_files, "release_stage": release_stage})
            st.success(t("Settings saved.", "تم حفظ الإعدادات."))
            st.rerun()
        if not can_edit_settings:
            st.info(t("Global settings are supervisor-managed in this prototype.", "الإعدادات العامة تدار من قبل المشرف في هذا النموذج الأولي."))
    with right:
        st.subheader(t("Enterprise Design Notes", "ملاحظات التصميم المؤسسي"))
        st.markdown(f"- {t('Minimal dashboard workflow: Upload PDF → AI Processing → Generated Knowledge Articles.', 'سير عمل بسيط: رفع PDF ← المعالجة بالذكاء الاصطناعي ← المقالات المعرفية الناتجة.')}" )
        st.markdown(f"- {t('Neutral palette with one accent color for clarity and readability.', 'لوحة ألوان محايدة مع لون أساسي واحد للوضوح وسهولة القراءة.')}" )
        st.markdown(f"- {t('Card-based topic articles for fast scanning and Word download.', 'بطاقات موضوعات لتسهيل المسح السريع وتنزيل ملفات Word.')}" )
        st.markdown(f"- {t('Privacy masking is enabled by default for enterprise review usage.', 'إخفاء البيانات مفعّل افتراضياً لمراجعة مؤسسية أكثر أماناً.')}" )
        st.markdown(f"- {t('Document search and grounded Q&A are available after parsing.', 'البحث في الوثيقة والأسئلة الموثقة متاحان بعد التحليل.')}")
        ocr_status = get_ocr_tool_status()
        st.markdown(f"- {t('OCR toolchain status', 'حالة أداة OCR')}: {ocr_status['message']}")

    st.subheader(t("Support Feedback", "ملاحظات الدعم"))
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
        feedback_items = list_feedback_items(limit=200)
        open_count = sum(1 for item in feedback_items if item["status"] == "open")
        status_card = f"""
        <div class="kb-note-card">
          <h3>{t('System Status', 'حالة النظام')}</h3>
          <p>{t('Open feedback items', 'ملاحظات مفتوحة')}: {open_count}</p>
          <p>{t('Audit visibility', 'وضوح التدقيق')}: {t('Enabled', 'مفعل')}</p>
          <p>{t('Word export', 'تصدير Word')}: {t('Ready', 'جاهز')}</p>
          <p>{t('Uploaded document search', 'البحث في الوثيقة')}: {t('Ready', 'جاهز')}</p>
        </div>
        """
        st.markdown(status_card, unsafe_allow_html=True)
        if is_privileged_reader():
            st.download_button(
                t("Export Feedback JSONL", "تصدير الملاحظات JSONL"),
                data=export_feedback_jsonl(),
                file_name="feedback-export.jsonl",
                mime="application/json",
                use_container_width=True,
            )
        else:
            st.caption(t("Feedback export is limited to supervisors and auditors.", "تصدير الملاحظات متاح للمشرفين والمدققين فقط."))

    if is_supervisor():
        st.subheader(t("Account Administration", "إدارة الحسابات"))
        users = list_users()
        for entry in users:
            with st.expander(f"{entry['display_name']} ({entry['user_id']}) | {entry['status']}"):
                st.write(f"{t('Role', 'الدور')}: {entry['role']}")
                st.write(f"{t('Failed login attempts', 'محاولات الدخول الفاشلة')}: {entry['failed_login_attempts']}")
                action_cols = st.columns(3)
                if action_cols[0].button(t("Activate", "تفعيل"), key=f"activate_{entry['user_id']}", use_container_width=True):
                    update_user_status(entry["user_id"], "active")
                    append_audit_event(st.session_state["auth_user"], "activate_user", "success", {"target_user": entry["user_id"]})
                    st.rerun()
                if action_cols[1].button(t("Deactivate", "تعطيل"), key=f"deactivate_{entry['user_id']}", use_container_width=True):
                    update_user_status(entry["user_id"], "inactive")
                    append_audit_event(st.session_state["auth_user"], "deactivate_user", "success", {"target_user": entry["user_id"]})
                    st.rerun()
                new_password = st.text_input(f"{t('Reset password for', 'إعادة تعيين كلمة المرور لـ')} {entry['user_id']}", type="password", key=f"pwd_{entry['user_id']}")
                if action_cols[2].button(t("Reset Password", "إعادة تعيين كلمة المرور"), key=f"reset_{entry['user_id']}", use_container_width=True) and new_password:
                    reset_user_password(entry["user_id"], new_password)
                    append_audit_event(st.session_state["auth_user"], "reset_password", "success", {"target_user": entry["user_id"]})
                    st.rerun()
        st.subheader(t("Support Inbox", "صندوق ملاحظات الدعم"))
        for item in list_feedback_items(limit=100):
            with st.expander(f"{item['created_at_utc']} | {item['category']} | {item['status']} | {item['user_id']}"):
                st.write(mask_sensitive_text(item["message"], bool(settings.get("privacy_masking_enabled", True))))
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


def render_audit() -> None:
    if not is_privileged_reader():
        st.error(t("Audit access is limited to supervisors and auditors.", "الوصول إلى التدقيق متاح للمشرفين والمدققين فقط."))
        return
    st.markdown(f"## {t('Audit', 'التدقيق')}")
    st.caption(t("Recent account, parsing, generation, export, and settings events.", "أحدث أحداث الحسابات والتحليل والتوليد والتصدير والإعدادات."))
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
    login_screen()
else:
    settings = current_settings()
    selected = sidebar_nav(settings)
    if selected == "workspace":
        render_workspace(settings)
    elif selected == "settings":
        render_settings(settings)
    else:
        render_audit()
