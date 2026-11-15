import { ConflictException, Injectable, NotFoundException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { InjectConnection, InjectModel } from '@nestjs/mongoose';
import { randomUUID } from 'crypto';
import * as bcrypt from 'bcryptjs';
import { Connection, Model } from 'mongoose';
import { Role, UserStatus } from '../../common/constants/roles.enum';
import { User } from '../users/schemas/user.schema';
import { Store } from '../stores/schemas/store.schema';
import { Employee } from './schemas/employee.schema';
import { CreateEmployeeDto } from './dto/create-employee.dto';

@Injectable()
export class EmployeeAccountsService {
  constructor(
    @InjectConnection() private readonly connection: Connection,
    @InjectModel(User.name) private readonly users: Model<User>,
    @InjectModel(Employee.name) private readonly employees: Model<Employee>,
    @InjectModel(Store.name) private readonly stores: Model<Store>,
    private readonly config: ConfigService,
  ) {}

  async create(dto: CreateEmployeeDto) {
    const email = dto.email.trim().toLowerCase();
    const employeeCode = dto.employeeCode.trim().toUpperCase();
    const userId = randomUUID();
    const employeeId = randomUUID();
    const password = await bcrypt.hash(dto.password, this.config.get<number>('bcryptCost') ?? 12);
    const session = await this.connection.startSession();

    try {
      // Tạo User và Employee trong cùng transaction để không có tài khoản thiếu cửa hàng.
      await session.withTransaction(async () => {
        const store = await this.stores.findOne({ storeId: dto.storeId, status: 'ACTIVE' }).session(session).lean().exec();
        if (!store) throw new NotFoundException('Không tìm thấy cửa hàng đang hoạt động');
        if (await this.users.exists({ email }).session(session)) throw new ConflictException('Email đã được sử dụng');
        if (await this.employees.exists({ employeeCode }).session(session)) throw new ConflictException('Mã nhân viên đã được sử dụng');
        await this.users.create([{
          userId, fullName: dto.fullName.trim(), email, phone: dto.phone,
          password, role: Role.STAFF, status: UserStatus.ACTIVE, refreshTokenHashes: [],
        }], { session });
        await this.employees.create([{
          employeeId, userId, employeeCode, employeeRole: dto.employeeRole.trim(), storeId: store.storeId,
        }], { session });
      });
    } catch (error) {
      // Unique index vẫn bảo vệ khi hai yêu cầu cùng dùng email hoặc mã nhân viên.
      if (typeof error === 'object' && error !== null && 'code' in error && error.code === 11000) {
        throw new ConflictException('Email hoặc mã nhân viên đã được sử dụng');
      }
      throw error;
    } finally {
      await session.endSession();
    }
    // Không trả mật khẩu hoặc hash mật khẩu trong API.
    return { userId, employeeId, fullName: dto.fullName.trim(), email, employeeCode, employeeRole: dto.employeeRole.trim(), storeId: dto.storeId };
  }

  async list() {
    const employeeRows = await this.employees.find().sort({ storeId: 1, employeeCode: 1 }).lean().exec();
    const userRows = await this.users.find({
      userId: { $in: employeeRows.map((employee) => employee.userId) },
      role: Role.STAFF,
    }).select('userId fullName email phone status').lean().exec();
    const usersById = new Map(userRows.map((user) => [user.userId, user]));
    return employeeRows.flatMap((employee) => {
      const user = usersById.get(employee.userId);
      return user ? [{
        userId: user.userId,
        employeeId: employee.employeeId,
        fullName: user.fullName,
        email: user.email,
        phone: user.phone ?? '',
        status: user.status,
        employeeCode: employee.employeeCode,
        employeeRole: employee.employeeRole,
        storeId: employee.storeId,
      }] : [];
    });
  }

  async updateStatus(userId: string, status: UserStatus) {
    const user = await this.users.findOneAndUpdate(
      { userId, role: Role.STAFF },
      { $set: { status, refreshTokenHashes: [] } },
      { new: true, projection: { userId: 1, status: 1 } },
    ).lean().exec();
    if (!user) throw new NotFoundException('Không tìm thấy tài khoản nhân viên');
    return { userId: user.userId, status: user.status };
  }

  async delete(userId: string) {
    const session = await this.connection.startSession();
    try {
      await session.withTransaction(async () => {
        const user = await this.users.findOne({ userId, role: Role.STAFF }).session(session).lean().exec();
        if (!user) throw new NotFoundException('Không tìm thấy tài khoản nhân viên');
        await this.employees.deleteOne({ userId }).session(session).exec();
        await this.users.deleteOne({ userId, role: Role.STAFF }).session(session).exec();
      });
    } finally {
      await session.endSession();
    }
    return { deleted: true, userId };
  }
}
