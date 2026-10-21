// The STRAUSS DRIVE mark: a burst of petals from one point, in the colours of the Strauss burst (warm on one side,
// greens on top, blues on the other). Drawn for this product in the spirit of the Strauss mark.
// `thinking` makes the petals open one after the other: the advisor's "working on it" signal.

// [angle from straight up, length, colour, lean]: lean makes each petal asymmetric, like a leaf
const PETALS: [number, number, string, number][] = [
  [-128, 13, 'var(--petal-orange)', 1],
  [-104, 16, 'var(--petal-red)', 1],
  [-78, 15, 'var(--petal-orange)', -1],
  [-52, 17, 'var(--petal-leaf)', 1],
  [-26, 19, 'var(--petal-green)', -1],
  [0, 20, 'var(--petal-leaf)', 1],
  [26, 19, 'var(--petal-green)', -1],
  [54, 17, 'var(--petal-sky)', 1],
  [82, 18, 'var(--petal-turq)', -1],
  [110, 16, 'var(--petal-blue)', 1],
  [136, 13, 'var(--petal-sky)', -1],
]

const petal = (len: number, lean: number) => {
  const a = len * 0.26 * (lean > 0 ? 1.15 : 0.85)
  const b = len * 0.26 * (lean > 0 ? 0.85 : 1.15)
  return `M0 0 C ${a} ${-len * 0.3} ${a * 0.9} ${-len * 0.78} 0 ${-len} C ${-b * 0.9} ${-len * 0.74} ${-b} ${-len * 0.32} 0 0 Z`
}

export function BrandMark({ size = 28, thinking = false, className }: { size?: number; thinking?: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 44 44" width={size} height={size} aria-hidden="true" focusable="false"
         className={`brandmark${thinking ? ' is-thinking' : ''}${className ? ` ${className}` : ''}`}>
      <g transform="translate(22 23)">
        {PETALS.map(([a, len, c, lean], i) => (
          <g key={i} transform={`rotate(${a})`}>
            <path d={petal(len, lean)} fill={c} className="petal" />
          </g>
        ))}
      </g>
    </svg>
  )
}

export function Wordmark() {
  return (
    <span className="wordmark latin" translate="no">
      <span className="wordmark-strauss">STRAUSS</span>
      <span className="wordmark-drive">DRIVE</span>
    </span>
  )
}
