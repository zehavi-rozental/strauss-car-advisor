// Hebrew names for the rep screen. Unknown codes are shown as is.
export const QUESTION_TITLES: Record<string, string> = {
  NEW_CAR_COPAY: 'השתתפות עובד ברכב חדש',
  CURRENT_COPAY: 'השתתפות עובד ברכב הנוכחי',
  CURRENT_USE_VALUE: 'שווי שימוש של הרכב הנוכחי',
  OCCUPATION_OPT_OUT: 'האם התפקיד מאפשר ויתור על רכב',
  MAINTENANCE_AMOUNT: 'סכום דמי אחזקה',
  GENERAL_QUESTION: 'שאלה כללית של עובד',
  ELIGIBILITY_GROUP: 'קבוצת זכאות שלא תואמת לתפקיד',
}
export const questionTitle = (code: string) => QUESTION_TITLES[code] ?? code

export const CONTEXT_LABELS: Record<string, string> = {
  group: 'קבוצה',
  car_label: 'רכב',
  suggested_amount: 'סכום בקטלוג (₪)',
  employee_stated_amount: 'העובד ציין (₪)',
  occupation_text: 'תפקיד',
  stated_group: 'קבוצה לפי העובד',
  table_group: 'קבוצה לפי טבלת העיסוקים',
}
export const MODE_LABELS: Record<string, string> = { CONFIRM: 'לאישור', REQUIRED: 'נדרשת תשובה' }
