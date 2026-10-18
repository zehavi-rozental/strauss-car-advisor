// Hebrew names for the rep screen. Unknown codes are shown as is.
export const QUESTION_TITLES: Record<string, string> = {
  NEW_CAR_COPAY: 'השתתפות עובד ברכב חדש',
  CURRENT_COPAY: 'השתתפות עובד ברכב הנוכחי',
  CURRENT_USE_VALUE: 'שווי שימוש של הרכב הנוכחי',
  OCCUPATION_OPT_OUT: 'האם התפקיד מאפשר ויתור על רכב',
  MAINTENANCE_AMOUNT: 'סכום דמי אחזקה',
  GENERAL_QUESTION: 'שאלה כללית של עובד',
}
export const questionTitle = (code: string) => QUESTION_TITLES[code] ?? code

export const CONTEXT_LABELS: Record<string, string> = {
  group: 'קבוצה',
  car_label: 'רכב',
  occupation_text: 'תפקיד',
}
export const MODE_LABELS: Record<string, string> = { CONFIRM: 'לאישור', REQUIRED: 'נדרשת תשובה' }
