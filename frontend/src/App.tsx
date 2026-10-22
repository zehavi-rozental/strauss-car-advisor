import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { EmployeePage } from './employee/EmployeePage'
import { RepPage } from './rep/RepPage'
import { EntryPage } from './shell/EntryPage'
import { TopBar } from './shell/TopBar'

export default function App() {
  const { pathname } = useLocation()
  return (
    <div className="app" data-zone={pathname.startsWith('/rep') ? 'rep' : 'employee'}>
      <TopBar />
      <Routes>
        <Route path="/" element={<EntryPage />} />
        <Route path="/employee" element={<EmployeePage />} />
        <Route path="/rep" element={<RepPage />} />
        <Route path="*" element={<Navigate to="/employee" replace />} />
      </Routes>
    </div>
  )
}
