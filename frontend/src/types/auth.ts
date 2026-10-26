export type UserRole = 'instructor' | 'student' | 'hr' | 'it' | 'manager' | 'employee';

export interface User {
  username: string;
  role: UserRole;
}

export interface AuthResponse {
  access: string;
  refresh: string;
  user: User;
}

export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
  bio: string;
}
