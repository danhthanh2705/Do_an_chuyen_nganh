const fs = require('fs');
const os = require('os');
const path = require('path');
const mongoose = require('mongoose');
const cloudinary = require('cloudinary').v2;

const products = [
  [1, 'Cà chua', 'j2hrzd5nkrjl9ecrg0ws', 24535],
  [2, 'Rau muống', 'q9caluyjjuy0vabaw1vt', 26663],
  [3, 'Cải thìa', 'cewptzfnrx2d9ruk1yyd', 18497],
  [4, 'Bắp cải', 'vzc5fn4uwk0u3sn90hhn', 23307],
  [5, 'Cà rốt', 'a8jpuvqajdnflzuz70j4', 48271],
  [6, 'Táo Fuji', 'yuqmk2wakhim4mgn1be7', 21990],
  [7, 'Chuối già', 'pay8esdj0wmd1kqsb0k8', 20296],
  [8, 'Cam sành', 'gqouh8ivvwcmtr99nrge', 37868],
  [9, 'Xoài cát', 'qnooreufvd9gr55erc7t', 7718],
  [10, 'Dưa hấu', 'tiel9sknm1cdcydyahjd', 23062],
  [11, 'Thịt heo ba rọi', 'kzpbellucgnwodbn4rqs', 26838],
  [12, 'Thịt bò thăn', 'q8qqonrqfc1mqu1gcumh', 40713],
  [13, 'Ức gà tươi', 'iwptsa2jryemctc6jpoq', 23747],
  [14, 'Cá basa phi lê', 'm9jyxaqr2nwcybamtll3', 12765],
  [15, 'Cá hồi Na Uy', 'kp8rnv6etgskxjhvlmld', 27556],
  [16, 'Sữa tươi ít đường', 'wawt6zczlxzd6l0nq9ow', 43653],
  [17, 'Sữa chua có đường', 'knvxyy69lw9urolk9rrh', 21141],
  [19, 'Phô mai lát', 'u5t0jheiggomht8iez08', 29057],
  [20, 'Bơ lạt', 'el0av9nvo9b7un9cndp5', 29797],
  [21, 'Gạo thơm ST25', 'qnwnu51powwbifeuvzyx', 55595],
  [22, 'Mì gói chua cay', 'unm69hf57ptsxueyofh0', 60672],
  [23, 'Nước mắm cá cơm', 'cxegkqvny1dlof0kfmh8', 42925],
  [24, 'Dầu ăn thực vật', 'hnpqj1nm8djpcnbjszfj', 14145],
  [25, 'Đường tinh luyện', 'bymdqqsau3l6bun8ooro', 26792],
  [26, 'Nước suối tinh khiết', 'suouzmouzis13bkeqodg', 14712],
  [27, 'Nước cam ép', 'glhtim3birdvgdjwv0qh', 35129],
  [28, 'Trà xanh không độ', 'wzim0uru56nk6ee6qlgt', 61135],
  [29, 'Cà phê sữa lon', 'ht2lqjzmzb5utis8arlk', 15719],
  [30, 'Nước khoáng có ga', 'sy5q0phcr1kwaei7zai6', 58478],
];

function productId(index) {
  return `00000007${index.toString(16).padStart(16, '0')}`;
}

async function main() {
  if (!process.env.MONGODB_URI) throw new Error('MONGODB_URI is not configured');
  if (!process.env.CLOUDINARY_URL) throw new Error('CLOUDINARY_URL is not configured');

  await mongoose.connect(process.env.MONGODB_URI);
  const collection = mongoose.connection.db.collection('products');
  const records = await collection.find({}).toArray();
  const assets = (await cloudinary.api.resources({
    type: 'upload',
    prefix: 'supermarket/products',
    max_results: 500,
  })).resources;
  const updates = [];

  for (const [index, productName, publicId, bytes] of products) {
    const id = productId(index);
    const record = records.find((item) => item.productId === id);
    const asset = assets.find((item) => item.public_id === `supermarket/products/${publicId}`);
    if (!record || record.productName !== productName) {
      throw new Error(`Product identity mismatch at ${id}`);
    }
    if (!asset || asset.bytes !== bytes || !asset.secure_url.startsWith('https://res.cloudinary.com/')) {
      throw new Error(`Cloudinary asset verification failed for ${productName}`);
    }
    if (!String(record.image).startsWith('https://images.unsplash.com/')) {
      throw new Error(`Current image changed unexpectedly for ${productName}`);
    }
    updates.push({ productId: id, productName, oldImage: record.image, image: asset.secure_url });
  }

  const egg = records.find((item) => item.productName === 'Trứng gà ta');
  if (!egg || assets.length !== 29 || updates.length !== 29) {
    throw new Error('Unexpected catalog or Cloudinary asset count');
  }

  if (!process.argv.includes('--apply')) {
    console.log(JSON.stringify({ mode: 'dry-run', updates: updates.length, unchanged: egg.productName }, null, 2));
    return;
  }

  const backupPath = path.join(os.tmpdir(), `supermarket-image-urls-${Date.now()}.json`);
  fs.writeFileSync(backupPath, JSON.stringify({
    createdAt: new Date().toISOString(),
    images: records.map(({ productId, productName, image }) => ({ productId, productName, image })),
  }, null, 2));

  const applied = [];
  try {
    for (const update of updates) {
      const result = await collection.updateOne(
        { productId: update.productId, image: update.oldImage },
        { $set: { image: update.image } },
      );
      if (result.modifiedCount !== 1) throw new Error(`Update did not match ${update.productName}`);
      applied.push(update);
    }
    const cloudCount = await collection.countDocuments({ image: /^https:\/\/res\.cloudinary\.com\// });
    if (cloudCount !== updates.length) throw new Error(`Expected ${updates.length} Cloudinary images, found ${cloudCount}`);
    console.log(JSON.stringify({ mode: 'applied', restored: cloudCount, unchanged: egg.productName, backupPath }, null, 2));
  } catch (error) {
    for (const update of applied.reverse()) {
      await collection.updateOne(
        { productId: update.productId, image: update.image },
        { $set: { image: update.oldImage } },
      );
    }
    throw error;
  }
}

main()
  .catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  })
  .finally(() => mongoose.disconnect());