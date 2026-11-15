import { Injectable, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PassportStrategy } from '@nestjs/passport';
import { ExtractJwt, Strategy } from 'passport-jwt';
import { Role, UserStatus } from '../../../common/constants/roles.enum';
import { AuthUser } from '../../../common/decorators/current-user.decorator';
import { UsersService } from '../../users/users.service';

interface AccessTokenPayload {
  sub: string;
  role: Role;
  storeId?: string;
  tokenType: 'access';
}

@Injectable()
export class JwtStrategy extends PassportStrategy(Strategy) {
  constructor(config: ConfigService, private readonly users: UsersService) {
    super({
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      ignoreExpiration: false,
      secretOrKey: config.get<string>('jwt.accessSecret')!,
    });
  }

  async validate(payload: AccessTokenPayload): Promise<AuthUser> {
    if (payload.tokenType !== 'access' || !payload.sub || !Object.values(Role).includes(payload.role)) {
      throw new UnauthorizedException('Access token không hợp lệ');
    }
    const account = await this.users.findById(payload.sub).select('role status').lean().exec();
    if (!account || account.role !== payload.role || account.status === UserStatus.LOCKED) {
      throw new UnauthorizedException('Tài khoản không còn hoạt động');
    }
    return { userId: payload.sub, role: payload.role, storeId: payload.storeId };
  }
}
