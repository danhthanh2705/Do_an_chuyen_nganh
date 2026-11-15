import { Body, Controller, Delete, Get, Param, Patch, Post } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import { IsEnum } from 'class-validator';
import { Role, UserStatus } from '../../common/constants/roles';
import { Roles } from '../../common/decorators/roles.decorator';
import { CreateEmployeeDto } from './dto/create-employee.dto';
import { EmployeeAccountsService } from './employee-accounts.service';

export class UpdateEmployeeStatusDto {
  @IsEnum(UserStatus) status: UserStatus;
}

@ApiTags('employees')
@ApiBearerAuth()
@Roles(Role.ADMIN)
@Controller('employees')
export class AdminEmployeesController {
  constructor(private readonly accounts: EmployeeAccountsService) {}

  @Get()
  list() {
    return this.accounts.list();
  }

  // Chỉ quản trị viên được tạo tài khoản nhân viên và gán vào cửa hàng.
  @Post()
  create(@Body() dto: CreateEmployeeDto) {
    return this.accounts.create(dto);
  }

  @Patch(':userId/status')
  updateStatus(@Param('userId') userId: string, @Body() dto: UpdateEmployeeStatusDto) {
    return this.accounts.updateStatus(userId, dto.status);
  }

  @Delete(':userId')
  delete(@Param('userId') userId: string) {
    return this.accounts.delete(userId);
  }
}
