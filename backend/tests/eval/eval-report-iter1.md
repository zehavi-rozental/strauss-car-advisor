# Conversation eval — turn model gemini-3.5-flash-lite

Judge: gemini-3.5-flash-lite, calibration 14/15 (counts)
- calibration miss: complaint_redirected: expected fail, judge pass ([])

Cases: 30, bot turns: 67

| criterion | score |
|---|---|
| answered_what_was_asked | 97.0% |
| correct_per_state_and_faq | 95.5% |
| no_invention | 100.0% |
| concise | 97.0% |
| natural_professional_hebrew | 98.5% |
| single_cta | 92.5% |
| final net / rep copay correct | 100% |

Latency p50 2.12 s, p95 6.89 s

Sources: {'llm': 58, 'template': 9}
SAY / guard outcomes: {'llm': 41, 'guard:progress': 2, 'llm:trimmed': 4, 'guard:claim,cta,progress': 1, 'llm:retry': 13, 'guard:offer': 1, 'fixed': 5}

## Code assertions
- one_long_message: no result at the end

## Failing turns
### live_s2_transcript turn 4 (llm, llm)
U: יש לי קורולה קרוס 2025 ואני משלמת עליה 300
B: רשמתי: קורולה קרוס (2025) בהשתתפות 300 ₪ בחודש. /  / אילו רכבים תרצו להשוות לקורולה קרוס (עד 3 רכבים מהקבוצה שלכם), או שתעדיפו שאיזה את הרכבים הכי משתלמים? /  / בחרו רכבים להשוואה או כתבו "הכי משתלם".
- natural_professional_hebrew: יש שגיאת ניסוח בסוף המשפט השני: 'או שתעדיפו שאיזה את הרכבים הכי משתלמים'.
- single_cta: הבוט נותן שתי הנחיות שונות בסוף: גם שאלת בחירה וגם שורה תחתונה 'בחרו רכבים להשוואה או כתבו הכי משתלם'.

### live_s2_transcript turn 5 (template, guard:claim,cta,progress)
U: תבדוק לי אטו 3 ו-MG4
B: בחרתם להשוות: MG4 (2025), ATTO 3 (2025). /  / כדי לחשב חסר לי: עמלות, בונוס, קרן השתלמות ונקודות זיכוי. בלי הערכים האלה אין חישוב, ואני לא מניח ערך במקום. / עמלות: בתלוש, בשורת העמלות. אם הן משתנות, אפשר סכום ממוצע של החודשים האחרונים. / בונוס: בתלוש, בשורת הבונוס או הפרמיה החודשית. / קרן השתלמות: בתלוש, בניכויים: קרן השתלמות, חלק העובד באחוזים. / נקודות זיכוי: בתלוש, בשורה נקודות זיכוי. /  / כתבו את הערך כשתמצאו אותו בתלוש, למשל: "עמלות 1,000".
- concise: התשובה ארוכה מדי וחורגת ממגבלת 5 השורות הקצרות, עקב פירוט מוגזם של כל ארבעת השדות.

### live_s2_transcript turn 8 (llm, llm:retry)
U: לא יודע
B: רשמתי שלא ידוע. /  / החישוב מחכה רק לעמלות, בונוס, קרן השתלמות ונקודות זיכוי, ואני לא מניח ערך במקומם. / אפשר לחזור לכאן בכל זמן ולכתוב את הערכים מתוך תלוש השכר. /  / כתבו את הנתונים כשתמצאו אותם בתלוש.
- single_cta: יש בסוף גם הנחיה וגם משפט שאינו קריאה ברורה אחת לפעולה.

### sunroof_question_mark_continue turn 2 (template, guard:offer)
U: להמשיך
B: על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה? התשובה תגיע לכאן. /  / בחרו "לשאול את הנציג" או "להמשיך בלי".
- answered_what_was_asked: העובד כתב 'להמשיך' בתגובה לשאלה האם לשאול את הנציג, והבוט חזר על אותה הש שאלה במקום להתקדם או לרשום את השאלה לנציג.
- correct_per_state_and_faq: המצב דורש איסוף פרופיל (קבוצה/תפקיד ושכר בסיס), והבוט תקוע בלולאת שאלות לנציג במקום להמשיך באיסוף הנתונים.
- single_cta: יש כאן גם שאלה וגם שתי אפשרויות בחירה (בחרו...), מה שמהווה מספר הנחיות/שאלות יחד.

### complaint_later turn 2 (llm, llm)
U: פנסיה 6%
B: רשמתי: פנסיה 6%. /  / יש רכיבי שכר נוספים — עמלות, בונוסים? ואילו הפרשות יורדות לכם: קרן השתלמות, נקודות זיכוי? וכמה ילדים עד גיל 23?
- correct_per_state_and_faq: הבוט שאל על נקודות זיכוי כ'הפרשות יורדות', מה ששגוי מבחינת מהות הסעיף (נקודות זיכוי אינן הפרשה יורדת).

### very_short turn 3 (template, guard:progress)
U: אין
B: רשמתי: אין עמלות, אין בונוס, אין קרן השתלמות, אין ילדים. /  / איזה רכב צמוד יש לכם היום, וכמה משלמים עליו בחודש? / חסר עוד: פנסיה, נקודות זיכוי. /  / כתבו את הדגם וכמה משלמים, למשל: "קורולה קרוס 2025, משלמת 300". אם אין רכב, כתבו "אין רכב".
- concise: התשובה ארוכה מדי ומכילה פירוט כפול ומכובס לגבי מה שחסר (גם שאל על הרכב וההשתתפות, גם ציין 'חסר עוד: פנסיה, נקודות זיכוי', וגם הוסיף דוגמה).
- single_cta: ההודעה כוללת מספר הנחיות ושאלות שונות (גם שאל על הרכב וההשתתפות, גם ציין מה חסר, וגם הביא דוגמה איך לכתוב), במקום קריאה לפעולה אחת ברורה.

### no_copay_before_car turn 3 (llm, llm)
U: אינני משתתף היום
B: רשמתי: אין לכם רכב צמוד היום. /  / איזה רכב צמוד יש לכם היום, ובאיזו שנת רישום?
- answered_what_was_asked: The employee said they don't have a car today ('אינני משתתף היום'), but the bot repeated the question asking what car they have today.
- correct_per_state_and_faq: Contradicts the employee's statement by asking again for the current car when they just said they don't have one.
- single_cta: Asks a question that contradicts the user's input.
