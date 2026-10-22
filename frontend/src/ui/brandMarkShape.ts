// The shape of the STRAUSS DRIVE mark, shared by the screen (BrandMark) and the PDF report.
// [angle from straight up, length, colour, lean]: lean makes each petal asymmetric, like a leaf.
export const PETALS: [number, number, string, number][] = [
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

export const petal = (len: number, lean: number) => {
  const a = len * 0.26 * (lean > 0 ? 1.15 : 0.85)
  const b = len * 0.26 * (lean > 0 ? 0.85 : 1.15)
  return `M0 0 C ${a} ${-len * 0.3} ${a * 0.9} ${-len * 0.78} 0 ${-len} C ${-b * 0.9} ${-len * 0.74} ${-b} ${-len * 0.32} 0 0 Z`
}
