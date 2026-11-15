import { UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Role, UserStatus } from '../../../common/constants/roles.enum';
import { UsersService } from '../../users/users.service';
import { JwtStrategy } from './jwt.strategy';

describe('JwtStrategy', () => {
  it('rejects an access token after its account is locked', async () => {
    const accountQuery = {
      select: jest.fn().mockReturnThis(),
      lean: jest.fn().mockReturnThis(),
      exec: jest.fn().mockResolvedValue({ role: Role.STAFF, status: UserStatus.LOCKED }),
    };
    const users = {
      findById: jest.fn().mockReturnValue(accountQuery),
    } as unknown as UsersService;
    const strategy = new JwtStrategy({ get: () => 'test-secret' } as unknown as ConfigService, users);

    await expect(strategy.validate({ sub: 'staff-1', role: Role.STAFF, tokenType: 'access' }))
      .rejects.toThrow(UnauthorizedException);
  });
});