const dns = require('node:dns');
try {
  const servers = dns.getServers();
  if (!servers || servers.length === 0 || (servers.length === 1 && servers[0] === '127.0.0.1')) {
    dns.setServers(['8.8.8.8', '1.1.1.1']);
  }
} catch {
  // ignore
}
const bcrypt = require('bcryptjs');
const mongoose = require('mongoose');
const { randomUUID } = require('crypto');
const { UserSchema } = require('../../dist/modules/users/schemas/user.schema');
const { CustomerSchema } = require('../../dist/modules/customers/schemas/customer.schema');
const { EmployeeSchema } = require('../../dist/modules/employees/schemas/employee.schema');
const { CartSchema } = require('../../dist/modules/carts/schemas/cart.schema');
const { MembershipSchema } = require('../../dist/modules/memberships/schemas/membership.schema');

function loadMongoUri() {
  if (process.env.MONGODB_URI) return process.env.MONGODB_URI;
  const fs = require('fs');
  const path = require('path');
  const files = [path.resolve(process.cwd(), '.env'), path.resolve(process.cwd(), '..', '.env')];
  for (const file of files) {
    if (!fs.existsSync(file)) continue;
    for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
      const match = line.match(/^\s*(?:export\s+)?MONGODB_URI\s*=\s*(.*?)\s*$/);
      if (match) {
        let uri = match[1].replace(/^(['"])(.*)\1$/, '$2').replace(/\s+#.*$/, '').trim();
        if (uri) return uri;
      }
    }
  }
  return process.env.MONGODB_URI || process.env.MONGO_URI;
}

async function seedAuthUsers() {
  const uri = loadMongoUri();
  if (!uri) throw new Error('Thiếu MONGODB_URI trong môi trường chạy seed');
  await mongoose.connect(uri);

  const User = mongoose.model('User', UserSchema);
  const Customer = mongoose.model('Customer', CustomerSchema);
  const Employee = mongoose.model('Employee', EmployeeSchema);
  const Cart = mongoose.model('Cart', CartSchema);
  const Membership = mongoose.model('Membership', MembershipSchema);
  const cost = Number(process.env.BCRYPT_COST || 12);
  const password = await bcrypt.hash('Muadee123', cost);
  const accounts = [
    { email: 'admin@muadee.vn', fullName: 'Quản trị Muadee', role: 'ADMIN', status: 'ACTIVE' },
    { email: 'staff@muadee.vn', fullName: 'Nhân viên Muadee', role: 'STAFF', status: 'ACTIVE' },
    { email: 'customer@muadee.vn', fullName: 'Khách hàng Muadee', role: 'CUSTOMER', status: 'ACTIVE' },
    { email: 'locked@muadee.vn', fullName: 'Tài khoản bị khóa', role: 'CUSTOMER', status: 'LOCKED' },
  ];

  for (const account of accounts) {
    const userId = randomUUID();
    const user = await User.findOneAndUpdate(
      { email: account.email },
      { $set: { ...account, password }, $setOnInsert: { userId, refreshTokenHashes: [] } },
      { new: true, upsert: true, setDefaultsOnInsert: true },
    );
    if (!user) throw new Error(`Không tạo được tài khoản seed ${account.email}`);

    if (account.role === 'STAFF') {
      await Employee.updateOne(
        { userId: user.userId },
        { $set: { userId: user.userId, employeeCode: 'MD-STAFF-001', employeeRole: 'Nhân viên cửa hàng', storeId: 's1' }, $setOnInsert: { employeeId: randomUUID() } },
        { upsert: true },
      );
    }
    if (account.role === 'CUSTOMER') {
      const customerId = randomUUID();
      const customer = await Customer.findOneAndUpdate(
        { userId: user.userId },
        { $setOnInsert: { customerId, userId: user.userId, address: '' } },
        { new: true, upsert: true },
      );
      await Cart.updateOne(
        { customerId: customer.customerId },
        { $setOnInsert: { cartId: randomUUID(), customerId: customer.customerId, totalAmount: 0 } },
        { upsert: true },
      );
      await Membership.updateOne(
        { customerId: customer.customerId },
        { $setOnInsert: { membershipId: randomUUID(), customerId: customer.customerId, memberCode: `MD${randomUUID().replace(/-/g, '').slice(0, 8).toUpperCase()}`, pointBalance: 0, memberLevel: 'Thường' } },
        { upsert: true },
      );
    }
  }

  console.log('Đã tạo/cập nhật 4 tài khoản mẫu Muadee (mật khẩu dev: Muadee123).');
  await mongoose.disconnect();
}

seedAuthUsers().catch(async (error) => {
  console.error('Seed auth thất bại:', error.stack || error);
  await mongoose.disconnect();
  process.exitCode = 1;
});
