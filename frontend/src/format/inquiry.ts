// The inquiry number both sides see (31/10): the employee's record in the chat and the representative's card show
// the same short reference, taken from the inquiry's own id (no new field, nothing to keep in step).
export const inquiryNumber = (id: string): string => id.replace(/-/g, '').slice(0, 8).toUpperCase()
