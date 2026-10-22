// The front door (/): one blue card with the opening artwork and two ways in. The employee goes to the
// consultation as before (no sign-in); the representative goes to the rep area, which keeps its own access
// code, checked by the server. This page holds no data and calls no API.
import { Link } from 'react-router-dom'
import { FlowCar } from '../art/FlowCar'
import { Icon } from '../ui/Icon'

export function EntryPage() {
  return (
    <div className="entry">
      <FlowCar mode="intro" />
      <div className="entry-content">
        <header className="entry-head">
          <h1>יועץ הרכב</h1>
          <p className="entry-lead">אילו רכבים מתאימים לכם, ואיך הבחירה משפיעה על הנטו.</p>
        </header>
        <nav className="entry-doors" aria-label="כניסה">
          <Link to="/employee" className="entry-door is-employee">
            <span className="entry-door-icon"><Icon name="road" size={24} /></span>
            <span className="entry-door-text">
              <span className="entry-door-name">כניסת עובד</span>
              <span className="entry-door-sub">ייעוץ רכב אישי</span>
            </span>
            <Icon name="arrowLeft" size={22} className="entry-door-go" />
          </Link>
          <Link to="/rep" className="entry-door is-rep">
            <span className="entry-door-icon"><Icon name="rep" size={24} /></span>
            <span className="entry-door-text">
              <span className="entry-door-name">כניסת נציג</span>
              <span className="entry-door-sub">טיפול בפניות ואישורים</span>
              <span className="entry-door-lock"><Icon name="lock" size={14} />כניסה מאובטחת בקוד גישה</span>
            </span>
            <Icon name="arrowLeft" size={22} className="entry-door-go" />
          </Link>
        </nav>
      </div>
    </div>
  )
}
