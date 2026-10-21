// One bar for the whole product: the STRAUSS DRIVE mark, the switch between the two areas (same tab, the
// conversation lives on the server and comes back on return), and the user menu.
// The rep area stays behind its access code (RepPage); this bar never carries data of either area.
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { reducedMotion, setReducedMotion } from '../art/motion'
import { BrandMark, Wordmark } from '../ui/BrandMark'
import { Icon } from '../ui/Icon'
import { hasRepToken, requestRepLogout } from './repSession'

export function TopBar() {
  const { pathname } = useLocation()
  const rep = pathname.startsWith('/rep')
  return (
    <header className="topbar">
      <div className="topbar-inner">
      <Link to="/employee" className="brand" aria-label="STRAUSS DRIVE">
        <BrandMark size={34} />
        <Wordmark />
      </Link>
      <nav className="zones" aria-label="מעבר מסך" data-active={rep ? 'rep' : 'employee'}>
        <span className="zones-thumb" aria-hidden="true" />
        <NavLink to="/employee" className="zone">
          <Icon name="road" size={18} />
          <span className="zone-text">
            <span className="zone-name">מסך העובד</span>
            <span className="zone-sub">ייעוץ רכב אישי</span>
          </span>
        </NavLink>
        <NavLink to="/rep" className="zone">
          <Icon name="rep" size={18} />
          <span className="zone-text">
            <span className="zone-name">מסך הנציג</span>
            <span className="zone-sub">טיפול בפניות ואישורים</span>
          </span>
        </NavLink>
      </nav>
      <UserMenu rep={rep} />
      </div>
    </header>
  )
}

function UserMenu({ rep }: { rep: boolean }) {
  const [open, setOpen] = useState(false)
  const [reduce, setReduce] = useState(reducedMotion)
  const [signedIn, setSignedIn] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const role = rep ? (signedIn ? 'מחוברים לאזור הנציג' : 'אזור הנציג מוגן בקוד גישה') : 'ייעוץ אישי לעובדי שטראוס'

  return (
    <div className="usermenu" ref={box}>
      <button type="button" className="usermenu-button" aria-haspopup="menu" aria-expanded={open}
              aria-label="תפריט משתמש" onClick={() => { setSignedIn(hasRepToken()); setOpen((o) => !o) }}>
        <Icon name="user" size={20} />
      </button>
      {open && (
        <div className="usermenu-pop" role="menu" aria-label="תפריט משתמש">
          <p className="usermenu-role">{role}</p>
          <button type="button" role="menuitemcheckbox" aria-checked={reduce} className="usermenu-item"
                  onClick={() => { setReducedMotion(!reduce); setReduce(!reduce) }}>
            <Icon name="motion" size={18} />
            <span>הפחתת תנועה</span>
            <span className={`switch${reduce ? ' is-on' : ''}`} aria-hidden="true" />
          </button>
          {rep && signedIn && (
            <button type="button" role="menuitem" className="usermenu-item"
                    onClick={() => { requestRepLogout(); setOpen(false) }}>
              <Icon name="logout" size={18} />
              <span>יציאה מאזור הנציג</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}
