import { useNavigate, useLocation } from 'react-router-dom';

export default function BackButton() {
  const navigate = useNavigate();
  const location = useLocation();

  const handleBack = () => {
    if (location.pathname.includes('/create') || location.pathname.includes('/edit')) {
      if (window.confirm("You have unsaved changes. Leave anyway?")) {
        sessionStorage.removeItem('chapter_draft');
        navigate(-1);
      }
    } else {
      navigate(-1);
    }
  };
  if (['/student/catalog', '/instructor/dashboard', '/login'].includes(location.pathname)) {
    return null;
  }

  return <button className="btn-back" onClick={handleBack}>← Back</button>;
}