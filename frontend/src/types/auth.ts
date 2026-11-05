export type UserRole = 'hr' | 'it' | 'manager' | 'employee';
export type OnboardingRole = UserRole;

export interface User {
  username: string;
  role: UserRole;
}

export interface AuthResponse {
  access: string;
  refresh: string;
  user: User;
}
