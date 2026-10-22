// The STRAUSS DRIVE mark: a burst of petals from one point, in the colours of the Strauss burst (warm on one side,
// greens on top, blues on the other). Drawn for this product in the spirit of the Strauss mark.
// `thinking` makes the petals open one after the other: the advisor's "working on it" signal.
import { PETALS, petal } from './brandMarkShape'

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
