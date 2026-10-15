// Hebrew display names for codes the server sends without a label. Unknown codes are shown as is.

const DRIVERS: Record<string, string> = {
  USE_VALUE: 'מס על שווי השימוש',
  ELECTRIC_ADDED: 'החזר רכב חשמלי',
  ELECTRIC_REMOVED: 'החזר רכב חשמלי שמתבטל',
  COPAY: 'ההשתתפות שלך',
  FAMILY_SHARE: 'חלקך במסלול המשפחתי',
  MAINTENANCE: 'דמי אחזקה',
}

const MISSING: Record<string, string> = {
  EMPLOYEE_GROUP: 'קבוצת זכאות',
  EMPLOYEE_BASE_SALARY: 'שכר בסיס',
  EMPLOYEE_PENSION_RATE: 'אחוז הפרשה לפנסיה',
  EMPLOYEE_CREDIT_POINTS: 'נקודות זיכוי',
  OCCUPATION_OPT_OUT: 'האם התפקיד מאפשר ויתור על רכב',
  CURRENT_USE_VALUE: 'שווי השימוש של הרכב היום',
  CURRENT_COPAY: 'ההשתתפות ברכב היום',
  CURRENT_MONTHLY_COST: 'עלות הרכב היום',
  CURRENT_MAINTENANCE: 'דמי האחזקה היום',
  NEW_CAR_USE_VALUE: 'שווי השימוש של הרכב החדש',
  NEW_CAR_COPAY: 'ההשתתפות ברכב החדש',
  NEW_CAR_MONTHLY_COST: 'עלות הרכב החדש',
  NEW_CAR_MAINTENANCE: 'סכום האחזקה',
}

// expects field names, for quick replies in a grouped question ("עמלות: אין")
const FIELDS: Record<string, string> = {
  commissions: 'עמלות',
  bonus: 'בונוס',
  pension_percent: 'פנסיה',
  study_fund_percent: 'קרן השתלמות',
  credit_points: 'נקודות זיכוי',
  children: 'ילדים',
  copay: 'השתתפות היום',
  use_value: 'שווי שימוש',
  maintenance: 'אחזקה',
}

export const driverLabel = (code: string) => DRIVERS[code] ?? code
export const fieldLabel = (field: string) => FIELDS[field] ?? field
export const missingLabel = (code: string) => MISSING[code] ?? code
