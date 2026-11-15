import { http, isMockEnabled, unwrapApiResponse } from './client';

export interface CreateEmployeeInput {
  fullName: string;
  email: string;
  phone: string;
  employeeCode: string;
  employeeRole: string;
  storeId: string;
  password: string;
}

export interface CreatedEmployee {
  userId: string;
  employeeId: string;
  fullName: string;
  email: string;
  employeeCode: string;
  employeeRole: string;
  storeId: string;
}

export interface EmployeeAccount extends CreatedEmployee {
  phone: string;
  status: 'ACTIVE' | 'LOCKED';
}

export const employeesApi = {
  create: async (input: CreateEmployeeInput): Promise<CreatedEmployee> => {
    if (isMockEnabled) throw new Error('Tắt chế độ mock để tạo tài khoản nhân viên thật.');
    return http.post<{ success: true; data: CreatedEmployee }>('/employees', input).then(unwrapApiResponse);
  },
  list: async (): Promise<EmployeeAccount[]> => {
    if (isMockEnabled) return [];
    return http.get<{ success: true; data: EmployeeAccount[] }>('/employees').then(unwrapApiResponse);
  },
  updateStatus: async (userId: string, status: EmployeeAccount['status']): Promise<void> => {
    if (isMockEnabled) throw new Error('Tắt chế độ mock để cập nhật tài khoản nhân viên.');
    await http.patch(`/employees/${userId}/status`, { status });
  },
  delete: async (userId: string): Promise<void> => {
    if (isMockEnabled) throw new Error('Tắt chế độ mock để xóa tài khoản nhân viên.');
    await http.delete(`/employees/${userId}`);
  },
};
