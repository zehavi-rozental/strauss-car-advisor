// The STRAUSS DRIVE mark: five petals from one point, one per meaning in the product (leaf green, turquoise,
// blue, red, orange). Drawn for this product; inspired by the Strauss burst, not a copy of it.
// `thinking` makes the petals open one after the other: the advisor's "working on it" signal.

const PETALS = [
  { a: -62, len: 19, c: 'var(--petal-green)' },
  { a: -31, len: 23, c: 'var(--petal-turq)' },
  { a: 0, len: 26, c: 'var(--petal-blue)' },
  { a: 31, len: 22, c: 'var(--petal-red)' },
  { a: 62, len: 18, c: 'var(--petal-orange)' },
]

const petal = (len: number) => {
  const w = len * 0.25
  return `M0 0 C ${w} ${-len * 0.28} ${w} ${-len * 0.72} 0 ${-len} C ${-w} ${-len * 0.72} ${-w} ${-len * 0.28} 0 0 Z`
}

export function BrandMark({ size = 28, thinking = false, className }: { size?: number; thinking?: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 40 40" width={size} height={size} aria-hidden="true" focusable="false"
         className={`brandmark${thinking ? ' is-thinking' : ''}${className ? ` ${className}` : ''}`}>
      <g transform="translate(20 33)">
        {PETALS.map((p, i) => (
          <g key={i} transform={`rotate(${p.a})`}>
            <path d={petal(p.len)} fill={p.c} className="petal" style={{ animationDelay: `${i * 0.12}s` }} />
          </g>
        ))}
      </g>
    </svg>
  )
}

export function Wordmark() {
  return (
    <span className="wordmark latin" translate="no">
      <span>STRAUSS</span>
      <span className="wordmark-drive">DRIVE</span>
    </span>
  )
}
