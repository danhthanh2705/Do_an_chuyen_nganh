import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowRight, Banknote, CheckCircle2, Clock3, QrCode, RefreshCw } from 'lucide-react';
import { ordersApi } from '../../api/orders';
import { paymentsApi } from '../../api/payments';
import { QrPaymentPanel } from '../../components/order/QrPaymentPanel';
import { PaymentMethodSelector } from '../../components/order/PaymentMethodSelector';
import { checkoutStore } from '../../store/checkoutStore';
import type { CustomerOrder, PaymentMethod } from '../../types/order';
import type { PaymentRequestResult } from '../../types/payment';
import { formatMoney } from '../../utils/money';

const PAYMENT_WINDOW_MS = 15 * 60_000;

export default function PaymentPage() {
  const { orderId = '' } = useParams();
  const navigate = useNavigate();
  const [order, setOrder] = useState<CustomerOrder>();
  const [payment, setPayment] = useState<PaymentRequestResult>();
  const [error, setError] = useState(false);
  const [remaining, setRemaining] = useState(PAYMENT_WINDOW_MS);
  const [retrying, setRetrying] = useState(false);
  const [checkingPayment, setCheckingPayment] = useState(false);
  const [paymentConfirmed, setPaymentConfirmed] = useState(false);
  const rawMethod = checkoutStore.getOrderPaymentMethod(orderId) ?? 'TRANSFER';
  const method: PaymentMethod = rawMethod === 'CARD' ? 'TRANSFER' : rawMethod;
  const [retryMethod, setRetryMethod] = useState<PaymentMethod>(method);

  useEffect(() => {
    let active = true;
    async function initialize() {
      try {
        const orderData = await ordersApi.get(orderId);
        let paymentData = checkoutStore.getPayment<PaymentRequestResult>(orderId);
        if (!paymentData || (!paymentData.qrCodeUrl && !paymentData.qrImageUrl && paymentData.paymentMethod === 'TRANSFER' && paymentData.paymentStatus === 'PENDING')) {
          paymentData = await paymentsApi.forOrder(orderId).catch(() => null);
        }
        if (!paymentData || paymentData.paymentMethod === 'CARD' || (!paymentData.qrCodeUrl && !paymentData.qrImageUrl && paymentData.paymentMethod === 'TRANSFER' && paymentData.paymentStatus === 'PENDING')) {
          const targetMethod: PaymentMethod = orderData.paymentMethod === 'CASH' ? 'CASH' : 'TRANSFER';
          if (targetMethod === 'TRANSFER') {
            paymentData = await paymentsApi.create(orderId, 'TRANSFER');
          }
        }
        if (!active || !paymentData) return;
        if (!paymentData.expiresAt && paymentData.paymentMethod === 'TRANSFER') paymentData = { ...paymentData, expiresAt: new Date(Date.now() + PAYMENT_WINDOW_MS).toISOString() };
        checkoutStore.setPayment(orderId, paymentData);
        setOrder(orderData);
        setPayment(paymentData);
        if (paymentData.paymentStatus === 'SUCCESS' || orderData.paymentStatus === 'SUCCESS') {
          setPaymentConfirmed(true);
        }
      } catch { if (active) setError(true); }
    }
    void initialize();
    return () => { active = false; };
  }, [orderId]);

  useEffect(() => {
    if (!payment || payment.paymentMethod !== 'TRANSFER' || payment.paymentStatus !== 'PENDING' || paymentConfirmed) return;
    const deadline = payment.expiresAt ? Date.parse(payment.expiresAt) : Date.now() + PAYMENT_WINDOW_MS;
    const tick = () => setRemaining(Math.max(0, deadline - Date.now()));
    tick();
    const countdown = window.setInterval(tick, 1000);
    const poll = window.setInterval(async () => {
      try {
        const result = await paymentsApi.get(payment.paymentId);
        const updated = { ...payment, ...result, expiresAt: result.expiresAt ?? payment.expiresAt };
        setPayment(updated); checkoutStore.setPayment(orderId, updated);
        if (result.paymentStatus === 'SUCCESS') {
          window.clearInterval(countdown);
          window.clearInterval(poll);
          setPaymentConfirmed(true);
        }
        if (result.paymentStatus === 'FAILED' || result.paymentStatus === 'REFUNDED') { window.clearInterval(countdown); window.clearInterval(poll); }
      } catch { /* Transient polling failures should not interrupt the payment screen. */ }
    }, 3000);
    return () => { window.clearInterval(countdown); window.clearInterval(poll); };
  }, [payment?.paymentId, payment?.paymentStatus, payment?.paymentMethod, orderId, navigate, paymentConfirmed]);

  async function handleCheckPayment() {
    if (!payment || checkingPayment) return;
    setCheckingPayment(true);
    try {
      const confirmed = await paymentsApi.confirmPayment(payment.paymentId);
      const updated: PaymentRequestResult = {
        ...payment,
        ...confirmed,
        paymentStatus: 'SUCCESS',
      };
      setPayment(updated);
      checkoutStore.setPayment(orderId, updated);
      setPaymentConfirmed(true);
      window.dispatchEvent(new CustomEvent('muadee:toast', { detail: 'Xác nhận thanh toán thành công!' }));
    } catch {
      window.dispatchEvent(new CustomEvent('muadee:toast', { detail: 'Không thể kiểm tra thanh toán, vui lòng thử lại.' }));
    } finally {
      setCheckingPayment(false);
    }
  }

  async function switchPaymentMethod(newMethod: PaymentMethod) {
    if (retrying) return;
    setRetrying(true);
    try {
      const next = await paymentsApi.create(orderId, newMethod, order?.totalAmount);
      checkoutStore.setOrderPaymentMethod(orderId, newMethod);
      checkoutStore.setPayment(orderId, next);
      setPayment(next);
      setRetryMethod(newMethod);
      setRemaining(PAYMENT_WINDOW_MS);
    } catch {
      setError(true);
    } finally {
      setRetrying(false);
    }
  }

  async function retryPayment() {
    if (retrying) return;
    setRetrying(true);
    try {
      if (retryMethod === 'CASH') {
        navigate(`/orders/${orderId}/success`, { replace: true });
        return;
      }
      const next = await paymentsApi.retry(orderId, 'TRANSFER', order?.totalAmount);
      checkoutStore.setOrderPaymentMethod(orderId, 'TRANSFER');
      checkoutStore.setPayment(orderId, next); setPayment(next); setRemaining(PAYMENT_WINDOW_MS);
    } catch { setError(true); }
    finally { setRetrying(false); }
  }

  const transferExpired = remaining <= 0 || (payment?.expiresAt ? Date.parse(payment.expiresAt) <= Date.now() : false);
  const paymentFailed = !paymentConfirmed && (payment?.paymentStatus === 'FAILED' || payment?.paymentStatus === 'REFUNDED' || transferExpired);
  const countdown = `${String(Math.floor(remaining / 60000)).padStart(2, '0')}:${String(Math.floor(remaining % 60000 / 1000)).padStart(2, '0')}`;
  if (error) return <div className="customer-shell payment-page"><div className="listing-message"><strong>Không tải được thông tin thanh toán</strong><p>Đơn hàng vẫn được lưu trong danh sách đơn hàng.</p><Link to="/orders">Xem đơn hàng</Link></div></div>;
  if (!order || !payment) return <div className="customer-shell payment-page"><span className="section-eyebrow">THANH TOÁN ĐƠN HÀNG</span><h1>Đang tải thông tin thanh toán</h1><p className="payment-order-meta">Vui lòng không đóng trang này.</p><div className="payment-loading" aria-label="Đang tải thanh toán"><i /></div></div>;

  if (paymentConfirmed || payment.paymentStatus === 'SUCCESS') {
    const paidAmountText = formatMoney(payment.amount ?? order.totalAmount);
    return (
      <div className="customer-shell payment-page">
        <section
          className="card-payment-panel"
          style={{
            maxWidth: '580px',
            margin: '1.5rem auto',
            padding: '2.25rem 2rem',
            textAlign: 'center',
            borderRadius: '1rem',
            border: '1px solid #bbf7d0',
            background: '#f0fdf4',
          }}
        >
          <div
            style={{
              width: '60px',
              height: '60px',
              borderRadius: '50%',
              background: '#16a34a',
              color: '#fff',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              marginBottom: '1rem',
            }}
          >
            <CheckCircle2 size={34} />
          </div>
          <span className="section-eyebrow" style={{ color: '#15803d', display: 'block', marginBottom: '0.25rem' }}>
            MUADEE CẢM ƠN BẠN
          </span>
          <h1 style={{ fontSize: '1.65rem', color: '#14532d', margin: '0.25rem 0 0.75rem' }}>
            Thanh toán thành công!
          </h1>
          <p style={{ fontSize: '1.05rem', color: '#166534', lineHeight: 1.6, margin: '0 0 1.5rem' }}>
            Đơn hàng <strong>{order.orderCode}</strong> của bạn với số tiền <strong>{paidAmountText}</strong> đã được xác nhận thanh toán và sẽ được giao trong thời gian sớm nhất!
          </p>
          <div
            style={{
              background: '#fff',
              borderRadius: '0.75rem',
              padding: '1rem 1.25rem',
              textAlign: 'left',
              marginBottom: '1.5rem',
              border: '1px solid #dcfce7',
              display: 'grid',
              gap: '0.6rem',
              fontSize: '0.95rem',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#6b7280' }}>Mã đơn hàng</span>
              <strong>{order.orderCode}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#6b7280' }}>Tổng thanh toán</span>
              <strong style={{ color: '#006241' }}>{paidAmountText}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#6b7280' }}>Trạng thái</span>
              <strong style={{ color: '#16a34a' }}>Đã thanh toán · Đang chuẩn bị giao</strong>
            </div>
            {order.shippingAddress && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem' }}>
                <span style={{ color: '#6b7280', flexShrink: 0 }}>Giao tới</span>
                <strong style={{ textAlign: 'right' }}>{order.shippingAddress}</strong>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center', flexWrap: 'wrap' }}>
            <Link
              to={`/orders/${orderId}`}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.5rem',
                background: '#006241',
                color: '#fff',
                padding: '0.7rem 1.4rem',
                borderRadius: '0.5rem',
                textDecoration: 'none',
                fontWeight: 600,
              }}
            >
              Theo dõi đơn hàng <ArrowRight size={16} />
            </Link>
            <Link
              to="/products"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.5rem',
                background: '#fff',
                color: '#1f2937',
                padding: '0.7rem 1.4rem',
                borderRadius: '0.5rem',
                border: '1px solid #d1d5db',
                textDecoration: 'none',
                fontWeight: 600,
              }}
            >
              Tiếp tục mua sắm
            </Link>
          </div>
        </section>
      </div>
    );
  }

  return <div className="customer-shell payment-page">
    <span className="section-eyebrow">THANH TOÁN ĐƠN HÀNG</span>
    {paymentFailed && <h1>Thanh toán chưa hoàn tất</h1>}
    <p className="payment-order-meta">Mã đơn {order.orderCode} · {formatMoney(order.totalAmount)}</p>
    {paymentFailed ? <section className="payment-failed-panel"><strong>Giao dịch chưa được xác nhận hoặc mã thanh toán đã hết hạn.</strong><p>Đơn hàng vẫn đang chờ thanh toán. Bạn có thể tạo lại giao dịch hoặc thanh toán sau.</p><PaymentMethodSelector value={retryMethod} onChange={setRetryMethod} /><div><button type="button" disabled={retrying} onClick={() => void retryPayment()}><RefreshCw size={15} />{retrying ? 'Đang tạo mã...' : 'Tạo lại giao dịch'}</button><Link to={`/orders/${orderId}`}>Thanh toán sau</Link></div></section> : payment.paymentMethod === 'CASH' ? <section className="card-payment-panel">
      <Banknote size={26} />
      <h2>Đơn hàng thanh toán khi nhận hàng</h2>
      <p>Đơn hàng này được đặt với phương thức thanh toán tiền mặt (COD) hoặc khi lấy hàng tại siêu thị.</p>
      <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem', flexWrap: 'wrap', justifyContent: 'center' }}>
        <button type="button" onClick={() => void switchPaymentMethod('TRANSFER')} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', background: '#006241', color: '#fff', border: 'none', padding: '0.625rem 1.25rem', borderRadius: '0.5rem', cursor: 'pointer', fontWeight: 600 }}>
          <QrCode size={16} /> Chuyển sang thanh toán VietQR
        </button>
        <Link to={`/orders/${orderId}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', background: '#f3f4f6', color: '#1f2937', padding: '0.625rem 1.25rem', borderRadius: '0.5rem', textDecoration: 'none', fontWeight: 600 }}>
          Xem chi tiết đơn hàng <ArrowRight size={16} />
        </Link>
      </div>
    </section> : <>
      <div className="payment-countdown"><Clock3 size={15} />Mã thanh toán còn hiệu lực <strong>{countdown}</strong></div>
      <QrPaymentPanel src={payment.qrImageUrl ?? payment.qrCodeUrl} orderId={order.orderCode} amount={payment.amount ?? order.totalAmount} bankName={payment.bankName} accountNumber={payment.accountNumber} accountName={payment.accountName} transferContent={payment.transferContent} />
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem', marginTop: '1.25rem' }}>
        <button
          type="button"
          disabled={checkingPayment}
          onClick={() => void handleCheckPayment()}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.5rem',
            background: '#006241',
            color: '#fff',
            border: 'none',
            padding: '0.75rem 1.75rem',
            borderRadius: '0.5rem',
            cursor: checkingPayment ? 'wait' : 'pointer',
            fontWeight: 600,
            fontSize: '1rem',
            boxShadow: '0 2px 8px rgba(0, 98, 65, 0.25)',
          }}
        >
          <CheckCircle2 size={18} />
          {checkingPayment ? 'Đang kiểm tra giao dịch...' : 'Kiểm tra thanh toán'}
        </button>
        <div className="payment-pending-status" role="status">Sau khi chuyển khoản thành công, vui lòng nhấn "Kiểm tra thanh toán"</div>
      </div>
    </>}
    <div className="payment-secondary-actions"><Link to={`/orders/${orderId}`}>Thanh toán sau</Link><Link to="/orders">Đơn hàng của tôi</Link></div>
  </div>;
}
