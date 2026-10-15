import { formatMoney } from './money'

/** Every amount sits in <bdi> so "+210 ₪" is not reordered inside RTL text. */
export function Money({ value, signed, className }: { value: string | number; signed?: boolean; className?: string }) {
  return <bdi className={className ? `money ${className}` : 'money'}>{formatMoney(value, { signed })}</bdi>
}
