import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Navbar from './components/Navbar';

// Auth pages
import Login from './pages/auth/Login';
import Register from './pages/auth/Register';

// Instructor pages
import Dashboard from './pages/instructor/Dashboard';
import CourseList from './pages/instructor/CourseList';
import CourseForm from './pages/instructor/CourseForm';
import CourseDetail from './pages/instructor/CourseDetail';
import ChapterForm from './pages/instructor/ChapterForm';
import StudentList from './pages/instructor/StudentList';
import LiveQuizHost from './pages/instructor/LiveQuizHost';
import SectionList from './pages/instructor/SectionList';
import SectionForm from './pages/instructor/SectionForm';
import QuizForm from './pages/instructor/QuizForm';
import QuizManage from './pages/instructor/QuizManage';
import QuestionForm from './pages/instructor/QuestionForm';

// Student pages
import Catalog from './pages/student/Catalog';
import MyCourses from './pages/student/MyCourses';
import CourseView from './pages/student/CourseView';
import ChapterReader from './pages/student/ChapterReader';
import LiveQuiz from './pages/student/LiveQuiz';
import ScheduleBuilder from './pages/student/ScheduleBuilder';
import ScheduleView from './pages/student/ScheduleView';
import QuizTake from './pages/student/QuizTake';
import QuizResult from './pages/student/QuizResult';

import './App.css';
import Workspace from './pages/onboarding/Workspace';
import TaskDetail from './pages/onboarding/TaskDetail';

function AppRoutes() {
  const { user } = useAuth();

  return (
    <Routes>
      <Route
        path="/"
        element={
          user
            ? ['hr', 'it', 'manager', 'employee'].includes(user.role) ? <Navigate to="/workspace" replace /> : user.role === 'instructor'
              ? <Navigate to="/instructor/dashboard" replace />
              : <Navigate to="/student/catalog" replace />
            : <Navigate to="/login" replace />
        }
      />

      {/* Auth */}
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/workspace" element={<ProtectedRoute><Workspace /></ProtectedRoute>} />
      <Route path="/workspace/tasks/:stepId" element={<ProtectedRoute><TaskDetail /></ProtectedRoute>} />

      {/* Instructor routes */}
      <Route path="/instructor/dashboard" element={
        <ProtectedRoute role="instructor"><Dashboard /></ProtectedRoute>
      } />
      <Route path="/instructor/courses" element={
        <ProtectedRoute role="instructor"><CourseList /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/create" element={
        <ProtectedRoute role="instructor"><CourseForm /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId" element={
        <ProtectedRoute role="instructor"><CourseDetail /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId/edit" element={
        <ProtectedRoute role="instructor"><CourseForm /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId/chapters/create" element={
        <ProtectedRoute role="instructor"><ChapterForm /></ProtectedRoute>
      } />
      <Route path="/instructor/chapters/:chapterId/edit" element={
        <ProtectedRoute role="instructor"><ChapterForm /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId/students" element={
        <ProtectedRoute role="instructor"><StudentList /></ProtectedRoute>
      } />
      <Route path="/instructor/quizzes/:quizId/live" element={
        <ProtectedRoute role="instructor"><LiveQuizHost /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId/sections" element={
        <ProtectedRoute role="instructor"><SectionList /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId/sections/create" element={
        <ProtectedRoute role="instructor"><SectionForm /></ProtectedRoute>
      } />
      <Route path="/instructor/sections/:sectionId/edit" element={
        <ProtectedRoute role="instructor"><SectionForm /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId/quizzes/create" element={
        <ProtectedRoute role="instructor"><QuizForm /></ProtectedRoute>
      } />
      <Route path="/instructor/courses/:courseId/quizzes/:quizId/edit" element={
        <ProtectedRoute role="instructor"><QuizForm /></ProtectedRoute>
      } />
      <Route path="/instructor/quizzes/:quizId" element={
        <ProtectedRoute role="instructor"><QuizManage /></ProtectedRoute>
      } />
      <Route path="/instructor/quizzes/:quizId/questions/create" element={
        <ProtectedRoute role="instructor"><QuestionForm /></ProtectedRoute>
      } />
      <Route path="/instructor/questions/:questionId/edit" element={
        <ProtectedRoute role="instructor"><QuestionForm /></ProtectedRoute>
      } />

      {/* Student routes */}
      <Route path="/student/catalog" element={
        <ProtectedRoute role="student"><Catalog /></ProtectedRoute>
      } />
      <Route path="/student/my-courses" element={
        <ProtectedRoute role={['student', 'employee']}><MyCourses /></ProtectedRoute>
      } />
      <Route path="/student/courses/:courseId" element={
        <ProtectedRoute role={['student', 'employee']}><CourseView /></ProtectedRoute>
      } />
      <Route path="/student/chapters/:chapterId" element={
        <ProtectedRoute role={['student', 'employee']}><ChapterReader /></ProtectedRoute>
      } />
      <Route path="/student/live" element={
        <ProtectedRoute role="student"><LiveQuiz /></ProtectedRoute>
      } />
      <Route path="/student/live/:roomCode" element={
        <ProtectedRoute role="student"><LiveQuiz /></ProtectedRoute>
      } />
      <Route path="/student/schedule" element={
        <ProtectedRoute role="student"><ScheduleView /></ProtectedRoute>
      } />
      <Route path="/student/schedule/build" element={
        <ProtectedRoute role="student"><ScheduleBuilder /></ProtectedRoute>
      } />
      <Route path="/student/quizzes/:quizId/take" element={
        <ProtectedRoute role={['student', 'employee']}><QuizTake /></ProtectedRoute>
      } />
      <Route path="/student/quizzes/:quizId/result" element={
        <ProtectedRoute role={['student', 'employee']}><QuizResult /></ProtectedRoute>
      } />
      <Route path="*" element={<div className="page-container"><h1>Page not found</h1><a href="/">Return to your workspace</a></div>} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        {/* Global UI Components rendered outside of Routes */}
        <Navbar />
        <main>
          <AppRoutes />
        </main>
      </AuthProvider>
    </BrowserRouter>
  );
}
