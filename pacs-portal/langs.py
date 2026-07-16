# ==========================================================
# langs.py — ინოვა PACS ლოკალიზაცია
# დამატება/შეცვლა: ამ ფაილში ცვლით ტექსტებს,
# main.py-ში შეიტანეთ: from langs import LANGS, T
# ==========================================================

LANGS = {

    # ══════════════════════════════
    # ქართული
    # ══════════════════════════════
    "ka": {
        # ზოგადი
        "app_title":        "ინოვა PACS",
        "clinic_name":      "ინოვა სამედიცინო ცენტრი",
        "welcome":          "მოგესალმებით",
        "logout":           "გამოსვლა",

        # ავტორიზაცია
        "login_subtitle":   "AUTHORIZATION",
        "username":         "მომხმარებელი",
        "password":         "პაროლი",
        "login_btn":        "შესვლა",
        "login_err":        "მონაცემები არასწორია",
        "conn_err":         "კავშირის შეცდომა",

        # საძიებო პანელი
        "fname":            "სახელი",
        "lname":            "გვარი",
        "pid":              "პირადი №",
        "modality":         "მოდალობა",
        "mod_all":          "ყველა",
        "date_from":        "დან",
        "date_to":          "მდე",
        "date_placeholder": "დდ.თთ.წწწწ",
        "search_btn":       "ძებნა",
        "pname":            "სახელი გვარი",
        "pname_ph":         "მაგ: გვარი სახელი",
        "date_range_err":   'შეცდომა: „დან“ თარიღი „მდე“ თარიღზე გვიან არ უნდა იყოს — გთხოვთ, გაასწოროთ.',

        # სწრაფი ფილტრები
        "qf_label":         "⚡ სწრაფი:",
        "qf_today":         "📅 დღეს",
        "qf_yesterday":     "🌙 გუშინ",
        "qf_month":         "📆 ბოლო 30 დღე",
        "qf_year":          "🗓 ამ წელს",
        "qf_clear":         "✕ გასუფთავება",
        "qf_total":         "სულ",
        "qf_studies":       "კვლევა",

        # ცხრილის სვეტები
        "col_id":           "ID",
        "col_patient":      "პაციენტი",
        "col_dob":          "დაბ. თარიღი",
        "col_modality":     "Modality",
        "col_study_date":   "კვლევის თარიღი",
        "col_institution":  "Institution",
        "col_description":  "Study Description",
        "col_images":       "Images / Series",
        "col_actions":      "Actions",
        "no_records":       "ჩანაწერი ვერ მოიძებნა",

        # პაგინაცია
        "page":             "გვერდი",
        "of":               "/",
        "prev":             "⬅ წინა",
        "next":             "შემდეგი ➡",

        # Share მოდალი
        "share_title":      "კვლევის გაზიარება",
        "share_email_lbl":  "SEND VIA EMAIL",
        "share_email_ph":   "პაციენტის Email",
        "share_send_btn":   "გაგზავნა",
        "share_sending":    "იგზავნება...",
        "share_ok":         "მეილი წარმატებით გაიგზავნა!",
        "share_err":        "შეცდომა გაგზავნისას",
        "share_conn_err":   "კავშირის შეცდომა",
        "share_invalid":    "შეიყვანეთ ვალიდური იმეილი",
        "share_or":         "ან",
        "share_qr_btn":     "🖨️ QR ინსტრუქციის ბეჭდვა",
        "share_close":      "დახურვა",

        # პაციენტის პორტალი
        "patient_subtitle": "PATIENT ACCESS",
        "patient_pid_lbl":  "პირადი ნომერი",
        "patient_pid_ph":   "11 ნიშნა კოდი",
        "patient_dob_lbl":  "დაბადების თარიღი",
        "patient_dob_ph":   "მაგ: 19900115",
        "patient_view_btn": "კვლევის ნახვა",
        "patient_hint":     "გთხოვთ შეიყვანოთ მონაცემები კვლევის გასახსნელად",
        "patient_err":      "მონაცემები არასწორია",
        "patient_verify_err": "ვერიფიკაციის შეცდომა",
    },

    # ══════════════════════════════
    # English
    # ══════════════════════════════
    "en": {
        # General
        "app_title":        "Innova PACS",
        "clinic_name":      "Innova Medical Center",
        "welcome":          "Welcome",
        "logout":           "Logout",

        # Auth
        "login_subtitle":   "AUTHORIZATION",
        "username":         "Username",
        "password":         "Password",
        "login_btn":        "Sign In",
        "login_err":        "Invalid credentials",
        "conn_err":         "Connection error",

        # Search panel
        "fname":            "First Name",
        "lname":            "Last Name",
        "pid":              "National ID",
        "modality":         "Modality",
        "mod_all":          "All",
        "date_from":        "From",
        "date_to":          "To",
        "date_placeholder": "DD.MM.YYYY",
        "search_btn":       "Search",
        "pname":            "Full Name",
        "pname_ph":         "e.g: Last First",
        "date_range_err":   "Error: the 'From' date must not be later than the 'To' date — please fix it.",

        # Quick filters
        "qf_label":         "⚡ Quick:",
        "qf_today":         "📅 Today",
        "qf_yesterday":     "🌙 Yesterday",
        "qf_month":         "📆 Last 30 days",
        "qf_year":          "🗓 This year",
        "qf_clear":         "✕ Clear",
        "qf_total":         "Total",
        "qf_studies":       "studies",

        # Table columns
        "col_id":           "ID",
        "col_patient":      "Patient",
        "col_dob":          "Birth Date",
        "col_modality":     "Modality",
        "col_study_date":   "Study Date",
        "col_institution":  "Institution",
        "col_description":  "Study Description",
        "col_images":       "Images / Series",
        "col_actions":      "Actions",
        "no_records":       "No records found",

        # Pagination
        "page":             "Page",
        "of":               "of",
        "prev":             "⬅ Prev",
        "next":             "Next ➡",

        # Share modal
        "share_title":      "Share Study",
        "share_email_lbl":  "SEND VIA EMAIL",
        "share_email_ph":   "Patient Email",
        "share_send_btn":   "Send Email",
        "share_sending":    "Sending...",
        "share_ok":         "Email sent successfully!",
        "share_err":        "Error sending email",
        "share_conn_err":   "Connection error",
        "share_invalid":    "Please enter a valid email",
        "share_or":         "or",
        "share_qr_btn":     "🖨️ Print QR Instructions",
        "share_close":      "Close",

        # Patient portal
        "patient_subtitle": "PATIENT ACCESS",
        "patient_pid_lbl":  "National ID",
        "patient_pid_ph":   "11-digit code",
        "patient_dob_lbl":  "Date of Birth",
        "patient_dob_ph":   "e.g. 19900115",
        "patient_view_btn": "View Study",
        "patient_hint":     "Please enter your details to access the study",
        "patient_err":      "Invalid credentials",
        "patient_verify_err": "Verification error",
    },
}

# ══════════════════════════════════════════════════════════
# T() — მთარგმნელი ფუნქცია
# გამოყენება:  T(lang, "col_patient")
# ══════════════════════════════════════════════════════════
def T(lang: str, key: str, fallback: str = "") -> str:
    """Returns translated string for given lang and key."""
    return LANGS.get(lang, LANGS["ka"]).get(key, fallback or key)
