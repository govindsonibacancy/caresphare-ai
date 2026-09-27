import { Navigate, createBrowserRouter } from 'react-router-dom'
import { ProtectedRoute } from '../auth/ProtectedRoute'
import { AppShell } from '../components/layout/AppShell'
import { AdminDocumentsPage } from '../features/admin/pages/AdminDocumentsPage'
import { AdminPage } from '../features/admin/pages/AdminPage'
import { AuditLogsPage } from '../features/audit/pages/AuditLogsPage'
import { AcceptInvitationPage } from '../features/auth/pages/AcceptInvitationPage'
import { ForgotPasswordPage } from '../features/auth/pages/ForgotPasswordPage'
import { LoginPage } from '../features/auth/pages/LoginPage'
import { RegisterPage } from '../features/auth/pages/RegisterPage'
import { ResetPasswordPage } from '../features/auth/pages/ResetPasswordPage'
import { VerifyEmailPage } from '../features/auth/pages/VerifyEmailPage'
import { ChatPage } from '../features/chat/pages/ChatPage'
import { DocumentsPage } from '../features/documents/pages/DocumentsPage'
import { ProfilePage } from '../features/profile/pages/ProfilePage'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  { path: '/accept-invitation', element: <AcceptInvitationPage /> },
  { path: '/verify-email', element: <VerifyEmailPage /> },
  { path: '/forgot-password', element: <ForgotPasswordPage /> },
  { path: '/reset-password', element: <ResetPasswordPage /> },
  {
    path: '/',
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/chat" replace /> },
          { path: 'chat', element: <ChatPage /> },
          { path: 'chat/:conversationId', element: <ChatPage /> },
          { path: 'documents', element: <DocumentsPage /> },
          { path: 'audit-logs', element: <AuditLogsPage /> },
          { path: 'admin', element: <AdminPage /> },
          { path: 'admin/documents', element: <AdminDocumentsPage /> },
          { path: 'profile', element: <ProfilePage /> },
        ],
      },
    ],
  },
])
