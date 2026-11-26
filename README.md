# Ticket Radar: חיפוש וניתוח טיקטים מ-ServiceNow

כלי מקומי שמושך טיקטים מ-ServiceNow לפי טווח תאריכים ושומר אותם במסד נתונים מקומי (SQLite עם אינדקס חיפוש טקסט מלא). מעליו יש ממשק בעברית עם:

- **חיפוש חכם**: מדביקים הודעת שגיאה או תיאור תקלה ומקבלים טיקטים דומים מדורגים לפי רלוונטיות. אם אין טיקט שמכיל את כל המילים, הכלי עובר לבד למצב "דומה".
- **חלוקה לצוותים**: לכל צוות מוצגים סה"כ הטיקטים ומספר הפתוחים. לחיצה על צוות מסננת לפיו.
- **פילטרים**: טווח תאריכים, פתוחים/סגורים, סוג טיקט ועדיפות. יש גם גרף לאורך זמן.
- **כרטיס טיקט** עם הערות הסגירה, קישור ל-ServiceNow ורשימת טיקטים דומים.
- **ייצוא ל-CSV**, שנפתח ב-Excel עם עברית תקינה.

**דרישות:** Python 3.11 ומעלה. אין תלויות חיצוניות (בלי `pip install`), והממשק לא טוען כלום מהאינטרנט, כך שהכלי מתאים לרשת סגורה.

## התחלה מהירה

```bash
python run.py serve --demo        # להציץ בממשק עם נתוני דמו, בלי ServiceNow

cp .env.example .env              # ולמלא SN_INSTANCE / SN_USERNAME
export SN_PASSWORD='...'          # ב-PowerShell:  $env:SN_PASSWORD='...'
python run.py check               # בדיקת חיבור והרשאות
python run.py                     # הפעלת הממשק: http://127.0.0.1:8765
```

בממשק לוחצים על **סנכרון** ובוחרים טווח תאריכים. אחרי המשיכה הראשונה מספיק ללחוץ על **עדכון מהיר**, שמושך רק טיקטים שנוצרו או השתנו מאז.

## פקודות

| פקודה | מה היא עושה |
|---|---|
| `python run.py` / `serve` | הפעלת הממשק. אפשרויות: `--demo`, `--host`, `--port`, `--no-browser` |
| `python run.py sync --from 2026-01-01 --to 2026-11-30` | משיכה לפי טווח. `--by-updated` מסנן לפי תאריך עדכון במקום פתיחה |
| `python run.py sync --incremental` | רק שינויים מאז הסנכרון האחרון. מתאים להרצה מתוזמנת (cron / Task Scheduler) |
| `python run.py check` | בדיקת חיבור והרשאות לכל טבלה |
| `python run.py config` | הצגת ההגדרות שבתוקף, עם סודות מוסתרים |

## הגדרות: משתני סביבה

כל ההגדרות נקראות ממשתני סביבה, או מקובץ `.env` ליד `run.py`. כל משתנה עם הסבר מופיע ב-`.env.example`.
סדר העדיפויות: **משתנה סביבה > `.env` > ברירת מחדל**. הקובץ `.env` לא עולה ל-git.

| משתנה | ברירת מחדל | הסבר |
|---|---|---|
| `SN_INSTANCE` | (חובה) | כתובת ה-ServiceNow |
| `SN_AUTH_TYPE` | `basic` | `basic` / `api_key` / `oauth` / `bearer` |
| `SN_USERNAME`, `SN_PASSWORD` | | עבור `basic`, וגם עבור `oauth` עם password grant |
| `SN_API_KEY` | | עבור `api_key`. נשלח ב-header בשם `x-sn-apikey` |
| `SN_CLIENT_ID`, `SN_CLIENT_SECRET` | | עבור `oauth` |
| `SN_TOKEN` | | עבור `bearer` |
| `SN_TABLES` | `incident,sc_req_item,change_request,problem` | |
| `SN_SCOPE` | `all` | `all` / `my_groups` / `groups` |
| `SN_SCOPE_GROUPS` | | עבור `groups`: שמות צוותים מופרדים בפסיק |
| `SN_SCOPE_USER` | | עבור `my_groups` עם משתמש שירות: שם המשתמש שלך |
| `SN_INCLUDE_JOURNAL` | `false` | למשוך גם הערות ו-work notes |
| `SN_EXTRA_QUERY` | | encoded query נוסף, למשל `priority<=3` |
| `SN_CA_BUNDLE` | | קובץ CA ארגוני (PEM) |
| `SN_VERIFY_SSL` | `true` | |
| `SN_DATE_QUERY_STYLE` | `script` | אם המשיכה מחזירה 0 טיקטים, נסה `plain` |
| `SN_TIMEOUT`, `SN_PAGE_SIZE` | `60`, `500` | |
| `TR_HOST`, `TR_PORT` | `127.0.0.1`, `8765` | `0.0.0.0` הופך את הממשק לנגיש מהרשת, **בלי סיסמה** |
| `TR_DB_PATH` | `tickets.db` | נתיב יחסי מחושב מתיקיית הפרויקט |

אם הארגון עובר דרך פרוקסי, משתמשים במשתנים הרגילים `HTTPS_PROXY` / `NO_PROXY`.

**הרשאות ב-ServiceNow:** מספיקה קריאה בלבד על הטבלאות, לרוב דרך ה-role `itil`. עבור `my_groups` צריך גם הרשאת קריאה ל-`sys_user_grmember`. אם ההתחברות ל-ServiceNow עוברת דרך SSO, צריך משתמש מקומי או API key.

## טיפים לחיפוש

- `"too many connections"`: חיפוש של ביטוי מדויק
- `redis -latency`: כל הטיקטים עם redis, חוץ מאלה שמופיעה בהם המילה latency
- `INC0012345`: קפיצה ישירה למספר טיקט
- בעברית, אותיות השימוש בתחילת מילה לא משנות את התוצאה: חיפוש "שרת" ימצא גם "לשרתים"
- הסינונים נשמרים בכתובת ה-URL, כך שאפשר לשמור חיפוש כסימנייה

## מבנה הפרויקט

```
run.py                      נקודת כניסה
ticket_radar/
  cli.py                    פקודות שורת הפקודה
  config.py                 הגדרות: dataclasses שנטענים ממשתני סביבה ומ-.env
  app.py                    חיבור הרכיבים (DB, חיפוש, סנכרון, web)
  demo.py                   נתוני דמו
  servicenow/
    client.py               Table API: דפדוף, ניסיונות חוזרים, SSL, פרוקסי
    auth.py                 Basic / API key / OAuth / Bearer
    models.py               Ticket והמרה מרשומת API
    queries.py              בניית encoded queries
    errors.py
  storage/
    database.py             סכמת SQLite ואינדקס FTS5
    repository.py           שמירה ושליפה של טיקטים
  search/
    query_parser.py         תחביר החיפוש → ביטויי FTS5
    hebrew.py               נרמול עברית (תחיליות וסיומות)
    filters.py              פילטרים → SQL
    engine.py               חיפוש מדורג, facets, טיקטים דומים, ייצוא
  sync/
    service.py              סנכרון מלא או מהיר, היקף צוותים, מעקב התקדמות
  web/
    server.py               שרת HTTP, ניתוב, קבצים סטטיים
    api.py                  JSON API
    static/                 index.html, css/, js/ (ES modules, בלי build)
tests/                      unittest, כולל שרת ServiceNow מדומה
```

## טסטים

```bash
python -m unittest discover -s tests -t .
```
