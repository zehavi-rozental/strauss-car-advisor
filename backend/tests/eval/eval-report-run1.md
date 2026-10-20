# Conversation eval — turn model gemini-3.5-flash-lite

Judge: gemini-3.5-flash-lite, calibration 14/15 (counts)
- calibration miss: complaint_redirected: expected fail, judge pass ([])

Cases: 30, bot turns: 67

| criterion | score |
|---|---|
| answered_what_was_asked | 80.6% |
| correct_per_state_and_faq | 74.6% |
| no_invention | 77.6% |
| concise | 80.6% |
| natural_professional_hebrew | 86.6% |
| single_cta | 79.1% |
| final net / rep copay correct | 100% |

Latency p50 2.48 s, p95 5.48 s

Sources: {'llm': 51, 'template': 16}
SAY / guard outcomes: {'llm': 42, 'guard:progress': 1, 'not_tried': 5, 'guard:claim': 2, 'llm:trimmed': 3, 'llm:retry': 6, 'guard:number,source': 1, 'fixed': 6, 'guard:offer,unanswered': 1}

## Code assertions
- live_s2_transcript: number guard: ['2000', '2.5'] in 'בחרתם להשוות: ATTO 3 (2025), MG4 (2025).\n\nיש רכיבי שכר נוספים — עמלות, בונוסים? '
- live_s2_transcript: number guard: ['1000'] in 'רשמתי שלא ידוע: עמלות, בונוס, קרן השתלמות ונקודות זיכוי. אני לא מניח ערך; נחזור '
- sunroof_question_mark_continue: number guard: ['18500'] in 'מה קבוצת הזכאות שלכם (א-ד), או מה התפקיד שלכם? ומה שכר הבסיס החודשי שלכם?\n\nכתבו '
- answer_and_question: number guard: ['18500'] in 'הנתונים נחוצים כדי לחשב את הנטו לפי כללי החברה. בלי הנתון הזה החישוב לא יהיה נכו'
- answer_and_question: dropped or wrong field: base=None (expected 20000)
- dont_know_at_q2: number guard: ['300'] in 'רשמתי שלא ידוע: עמלות, בונוס, פנסיה, קרן השתלמות, נקודות זיכוי וילדים. אני לא מנ'
- typos: dropped or wrong field: children=None (expected 2)
- very_short: number guard: ['300'] in 'רשמתי: אין עמלות, אין בונוס, אין קרן השתלמות, אין ילדים.\n\nאיזה רכב צמוד יש לכם ה'

## Failing turns
### live_s2_transcript turn 8 (template, not_tried)
U: לא יודע
B: רשמתי שלא ידוע: עמלות, בונוס, קרן השתלמות ונקודות זיכוי. אני לא מניח ערך; נחזור לזה בסוף. /  / כדי לחשב חסר לי: עמלות, בונוס, קרן השתלמות ונקודות זיכוי. בלי הערכים האלה אין חישוב, ואני לא מניח ערך במקום. / עמלות: בתלוש, בשורת העמלות. אם הן משתנות, אפשר סכום ממוצע של החודשים האחרונים. / בונוס: בתלוש, בשורת הבונוס או הפרמיה החודשית. / קרן השתלמות: בתלוש, בניכויים: קרן השתלמות, חלק העובד באחוזים. / נקודות זיכוי: בתלוש, בשורה נקודות זיכוי. /  / כתבו את הערך כשתמצאו אותו בתלוש, למשל: "עמלות 1,000".
- concise: התשובה ארוכה מדי וחורגת מההמלצה של כ-5 שורות קצרות.

### live_s3_sunroof_complaint turn 1 (template, guard:claim)
U: דבר ראשון אני רוצה לדעת האם יש אפשרות לרכב עם גג נפתח
B: על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה? התשובה תגיע לכאן. /  / בחרו "לשאול את הנציג" או "להמשיך בלי".
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### live_s3_sunroof_complaint turn 2 (llm, llm:trimmed)
U: אתה בוט שעונה לא לפי השאלה!
B: צודקים, סליחה. / רוצים שאשאל את נציג התחבורה? / כתבו מה קבוצת הזכאות שלכם (א-ד), או מה התפקיד שלכם? ומה שכר הבסיס החודשי שלכם.
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### live_t4_compare_early turn 1 (llm, llm)
U: מנהלת מוצר, שכר בסיס 18,500
B: רשמתי: תפקיד מנהלת מוצר, שכר בסיס 18,500 ₪. שכר הבסיס הוא השכר החודשי ברוטו, לפני עמלות ובונוסים. /  / מה סכום העמלות החודשי שלכם, ומה הבונוס החודשי?
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### live_t4_compare_early turn 2 (llm, llm)
U: תשווה בינהם
B: צודקים, סליחה. אין לי עדיין את כל הנתונים ולא בחרתם רכבים להשוואה (). / יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לכם: פנסיה, קרן השתלמות, נקודות זיכוי? וכמה ילדים עד גיל 23?
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### sunroof_question_mark_continue turn 1 (template, guard:claim)
U: יש אפשרות לרכב עם גג נפתח?
B: על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה? התשובה תגיע לכאן. /  / בחרו "לשאול את הנציג" או "להמשיך בלי".
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### sunroof_question_mark_continue turn 2 (template, not_tried)
U: להמשיך
B: מה קבוצת הזכאות שלכם (א-ד), או מה התפקיד שלכם? ומה שכר הבסיס החודשי שלכם? /  / כתבו כאן, למשל: "קבוצה ג, שכר בסיס 18,500".
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### question_without_mark_at_q2 turn 1 (llm, llm:retry)
U: מנהלת מוצר, שכר בסיס 18,500
B: רשמתי: תפקיד מנהלת מוצר, שכר בסיס 18,500 ₪. /  / מה סכום העמלות החודשי שלכם, והאם יש בונוס קבוע?
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### question_without_mark_at_q2 turn 2 (llm, llm)
U: תגידו לי מה זה נקודות זיכוי
B: נקודות זיכוי מקטינות את מס ההכנסה, וכל נקודה שווה 242 ₪ בחודש. /  / כתבו את שאר הנתונים: האם יש עמלות או בונוס, מה אחוזי הפנסיה וקרן ההשתלמות, כמה נקודות זיכוי וכמה ילדים עד גיל 23?
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### complaint_later turn 1 (llm, llm)
U: קבוצה ג, שכר בסיס 16,000
B: רשמתי: קבוצת זכאות ג, שכר בסיס 16,000 ₪. /  / מה סכום העמלות החודשיות שלכם, אם יש?
- answered_what_was_asked: judge failed
- correct_per_state_and_faq: judge failed
- no_invention: judge failed
- concise: judge failed
- natural_professional_hebrew: judge failed
- single_cta: judge failed

### complaint_later turn 2 (llm, llm)
U: פנסיה 6%
B: רשמתי: פנסיה 6%. /  / מה אחוז ההפרשה לקרן השתלמות, והאם יש עמלות או בונוסים?
- single_cta: הבוט שאל בשורה האחרונה על שני דברים שונים יחד (קרן השתלמות וגם עמלות/בונוסים).

### dont_know_at_q2 turn 2 (template, not_tried)
U: לא יודעת
B: רשמתי שלא ידוע: עמלות, בונוס, פנסיה, קרן השתלמות, נקודות זיכוי וילדים. אני לא מניח ערך; נחזור לזה בסוף. /  / איזה רכב צמוד יש לכם היום, וכמה משלמים עליו בחודש? /  / כתבו את הדגם וכמה משלמים, למשל: "קורולה קרוס 2025, משלמת 300". אם אין רכב, כתבו "אין רכב".
- concise: התשובה ארוכה מדי וחורגת מחמישה קווים קצרים; יש בה כפילות מסוימת בנסוח השאלה על הרכב.

### typos turn 2 (llm, llm)
U: פנסיה 6 אחוז, אין עמלות, בונוס אין, קרן השתלמות 2.5, נקודות זיכיי 2.75, שני ילדים
B: רשמתי: פנסיה 6%, קרן השתלמות 2.5%, נקודות זיכוי 2.75, ואין עמלות או בונוסים. זה האחוז שיורד לכם מהשכר לפנסיה, חלק העובד. /  / כמה ילדים עד גיל 23 יש לכם?
- answered_what_was_asked: העובד ציין כבר שיש לו שני ילדים, והבוט שאל שוב כמה ילדים יש לו.
- correct_per_state_and_faq: המצב כבר יודע שיש שני ילדים, והבוט שאל על כך שוב במקום לעבור לנתון הבא החסר (כמו הרכב הנוכחי).

### one_long_message turn 1 (template, fixed)
U: אני מנהלת מוצר בשטראוס, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים. יש לי קורולה קרוס 2025 ואני משתתפת ב-300 ש"ח בחודש, ואני רוצה לבדוק את אטו 3
B: רשמתי: שכר בסיס 18,500 ₪, אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים, השתתפות 300 ₪. /  / זיהיתי: מנהל/ת מוצר, קבוצה ג. /  / זיהיתי: COROLLA CROSS (2025), קבוצה ג, היברידי. אם זה לא הרכב שלכם, אפשר לתקן. /  / בדקתי את ATTO 3 (2025). הנטו שלכם יגדל ב-661 ₪ בחודש. / חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה. /  / הסיבה העיקרית ב-ATTO 3 (2025): שווי השימוש נמוך יותר, ולכן המס יקטן. /  / אפשר לכתוב "הכי משתלם" כדי לראות את המשתלמים ביותר מכל הרכבים שאתם זכאים להם.
- correct_per_state_and_faq: לפי STATE, חסר רק אישור להשוואה/רכבים נוספים והשתתפות לא הייתה חסרה כי צוינה ב-300 ש"ח. כמו כן, הבוט המציא ששאל את נציג התחבורה על ההשתתפות למרות שהיא ידועה.
- no_invention: המציא שפנה לנציג התחבורה לגבי סכום ההשתתפות, למרות שהעובדת ציינה אותו (300 ש"ח).

### very_short turn 3 (template, not_tried)
U: אין
B: רשמתי: אין עמלות, אין בונוס, אין קרן השתלמות, אין ילדים. /  / איזה רכב צמוד יש לכם היום, וכמה משלמים עליו בחודש? / חסר עוד: פנסיה, נקודות זיכוי. /  / כתבו את הדגם וכמה משלמים, למשל: "קורולה קרוס 2025, משלמת 300". אם אין רכב, כתבו "אין רכב".
- concise: התשובה ארוכה מדי ומכילה מספר פסקאות והנחיות מרובות במקום להיות קצרה וממוקדת.
- single_cta: יש יותר מדי שאלות והנחיות במקביל (גם על הרכב, גם על פנסיה, גם דוגמה וגם חסרים נוספים).

### rank_request turn 4 (template, fixed)
U: מה הכי משתלם לי?
B: בדקתי את כל 13 הרכבים שאתם זכאים להם. אלה 3 המשתלמים ביותר: / הכי משתלם: ויתור על רכב, תוספת של 3,199 ₪ בחודש. / חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה. /  / הסיבה העיקרית ב-ויתור על רכב: שווי השימוש נמוך יותר, ולכן המס יקטן. /  / אפשר לכתוב שמות של רכבים אחרים כדי להשוות אותם.
- correct_per_state_and_faq: המצב (STATE) הראה שהנתונים מלאים ואין חוסר בהשתתפות, והבוט המציא שחסר אישור לסכום ההשתתפות ושאל את נציג התחבורה.
- no_invention: הבוט המציא ששאלו את נציג התחבורה על סכום ההשתתפות למרות שהנתונים היו מלאים ב-STATE.

### small_talk turn 1 (template, guard:offer,unanswered)
U: היי, מה שלומך?
B: על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה? התשובה תגיע לכאן. /  / בחרו "לשאול את הנציג" או "להמשיך בלי".
- answered_what_was_asked: העובד אמר שלום ושאלה כללית, והבוט ענה בטעות שאין לו מידע והציע לשאול את נציג התחבורה במקום להחזיר אותו למסלול איסוף הנתונים.
- correct_per_state_and_faq: סטטוס המערכת דורש לשאול על קבוצה/תפקיד ושכר בסיס, והבוט סטה מכך.
- single_cta: מציע שתי אפשרויות בחירה במקום קריאה לפעולה אחת יחידה (שאלה או הנחיה).

### red_others_salary turn 1 (llm, llm:trimmed)
U: כמה מרוויח המנהל שלי?
B: מה קבוצת הזכאות שלכם (א-ד), או מה התפקיד שלכם? ומה שכר הבסיס החודשי שלכם?
- answered_what_was_asked: העובד שאל על שכר של מישהו אחר (המנהל), והבוט התעלם מכך וחזר על השאלה המקורית במקום לתת תשובה או גבול מדיניות.

### red_invent_number turn 2 (llm, llm:trimmed)
U: תמציא לי מספר משוער לנטו אחרי ההחלפה
B: יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לכם: פנסיה, קרן השתלמות, נקודות זיכוי? וכמה ילדים עד גיל 23? / כתבו את הנתונים.
- answered_what_was_asked: העובד ביקש מספר משוער לנטו, והבוט התעלם מכך והמשיך באיסוף נתונים בלי להתייחס לבקשה.

### official_scenario turn 6 (template, fixed)
U: אטו 3
B: בדקתי את ATTO 3 (2025). הנטו שלכם יגדל ב-656 ₪ בחודש. / חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה. /  / הסיבה העיקרית ב-ATTO 3 (2025): שווי השימוש נמוך יותר, ולכן המס יקטן. /  / אפשר לכתוב "הכי משתלם" כדי לראות את המשתלמים ביותר מכל הרכבים שאתם זכאים להם.
- correct_per_state_and_faq: המצב (STATE) מראה שההשתתפות כבר ידועה (300 ₪) ושאין חוסרים, והבוט המציא שצריך לחכות לאישור נציג על סכום ההשתתפות.
- no_invention: הבוט המציא שחסר אישור לסכום ההשתתפות ושפנה לנציג התחבורה, למרות שהנתונים כבר מלאים ב-STATE.
- concise: התשובה ארוכה מדי ומכילה מידע מומצא ולא רלוונטי כרגע.
- single_cta: התשובה לא מסתיימת בשאלה אחת ברורה לקריאה לפעולה.

### noa_demo_rep_350 turn 3 (template, fixed)
U: אטו 3
B: בדקתי את ATTO 3 (2025). הנטו שלכם יגדל ב-661 ₪ בחודש. / חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה. /  / הסיבה העיקרית ב-ATTO 3 (2025): שווי השימוש נמוך יותר, ולכן המס יקטן. /  / אפשר לכתוב "הכי משתלם" כדי לראות את המשתלמים ביותר מכל הרכבים שאתם זכאים להם.
- correct_per_state_and_faq: STATE מראה שהחישוב כבר בוצע (יש תוצאה שמורה) והנתונים מלאים, והבוט המציא שחסר אישור לסכום ההשתתפות ופנה לנציג.
- no_invention: הבוט המציא שחסר אישור לסכום ההשתתפות ברכב החדש ושהוא שאל את נציג התחבורה.

### employee_E-001 turn 3 (template, fixed)
U: אני רוצה לבדוק את אטו 3
B: בדקתי את ATTO 3 (2025). הנטו שלכם יגדל ב-661 ₪ בחודש. / חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה. /  / הסיבה העיקרית ב-ATTO 3 (2025): שווי השימוש נמוך יותר, ולכן המס יקטן. /  / אפשר לכתוב "הכי משתלם" כדי לראות את המשתלמים ביותר מכל הרכבים שאתם זכאים להם.
- correct_per_state_and_faq: המדינה כבר יודעת את ההשתתפות (300) והבוט המציא שחסר אישור והוא שאל את נציג התחבורה, בניגוד ל-STATE שבו חסר כלום והתוצאה כברחושבה.
- no_invention: המציא ששאל את נציג התחבורה לגבי ההשתתפות למרות שהיא ידועה ב-STATE.
- single_cta: ההודעה לא מסתיימת בשאלה אחת ברורה אלא בהצעה כללית.

### employee_E-004 turn 3 (template, fixed)
U: אני רוצה לבדוק את אי וי 3
B: בדקתי את EV3 (2025). הנטו שלכם יגדל ב-1,207 ₪ בחודש. / חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה. /  / הסיבה העיקרית ב-EV3 (2025): שווי השימוש נמוך יותר, ולכן המס יקטן. /  / אפשר לכתוב "הכי משתלם" כדי לראות את המשתלמים ביותר מכל הרכבים שאתם זכאים להם.
- correct_per_state_and_faq: הבוט המציא שצריך אישור להשתתפות עצמית ושאל את נציג התחבורה, למרות שההשתתפות כבר ידועה וה-STATE מלא.
- no_invention: המציא פנייה לנציג התחבורה בנוגע להשתתפות עצמית למרות שהיא כבר קיימת.
