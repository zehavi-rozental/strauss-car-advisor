import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { EmployeePage } from './employee/EmployeePage'
import { RepPage } from './rep/RepPage'

/** Quick switch between the two screens during the demo. */
function ScreenSwitch() {
  return (
    <nav className="screen-switch" aria-label="מעבר מסך">
      <NavLink to="/employee">מסך העובד</NavLink>
      <span aria-hidden="true">/</span>
      <NavLink to="/rep">מסך הנציג</NavLink>
    </nav>
  )
}

export default function App() {
  return (
    <>
      <ScreenSwitch />
      <Routes>
        <Route path="/" element={<Navigate to="/employee" replace />} />
        <Route path="/employee" element={<EmployeePage />} />
        <Route path="/rep" element={<RepPage />} />
        <Route path="*" element={<Navigate to="/employee" replace />} />
      </Routes>
    </>
  )
}
