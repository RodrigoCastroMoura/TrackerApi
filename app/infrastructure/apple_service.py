import { useCallback, useEffect, useRef, useState } from 'react';
import { Platform } from 'react-native';
import {
  initConnection,
  endConnection,
  fetchProducts,
  requestPurchase,
  purchaseUpdatedListener,
  purchaseErrorListener,
  finishTransaction,
  getAvailablePurchases,
  type Purchase,
} from 'react-native-iap';
import { SubscriptionsService } from '@/api/subscriptions/subscriptions.service';

export const IS_IOS = Platform.OS === 'ios';

type Options = {
  productIds: string[];
  onVerified: () => void;
  onError: (message: string) => void;
};

export function useAppleIap({ productIds, onVerified, onError }: Options) {
  const [ready, setReady] = useState(false);
  const [products, setProducts] = useState<Record<string, any>>({});
  const [purchasing, setPurchasing] = useState(false);
  const cbRef = useRef({ onVerified, onError });
  cbRef.current = { onVerified, onError };
  const processing = useRef(new Set<string>());
  // true só enquanto há uma compra/restauração iniciada pelo usuário nesta tela.
  // Reentregas e renovações em segundo plano são validadas em silêncio.
  const userInitiated = useRef(false);

  const verifyAndFinish = useCallback(async (purchase: Purchase) => {
    const p = purchase as any;
    const transactionId: string = p.transactionId ?? p.id;
    if (!transactionId || processing.current.has(transactionId)) return;
    processing.current.add(transactionId);
    try {
      await SubscriptionsService.verifyApplePurchase({
        transaction_id: transactionId,
        product_id: p.productId,
      });
      await finishTransaction({ purchase, isConsumable: false });
      if (userInitiated.current) {
        userInitiated.current = false;
        cbRef.current.onVerified();
      }
    } catch (e: any) {
      // NÃO finaliza: a Apple reentrega a transação no próximo launch.
      if (userInitiated.current) {
        userInitiated.current = false;
        cbRef.current.onError(
          e?.response?.data?.message ?? 'Não foi possível validar sua compra. Tente restaurar compras.',
        );
      }
    } finally {
      processing.current.delete(transactionId);
      setPurchasing(false);
    }
  }, []);

  useEffect(() => {
    // Os planos chegam da API depois do primeiro render; sem IDs não há o que buscar.
    if (!IS_IOS || productIds.length === 0) return;
    let alive = true;

    const updateSub = purchaseUpdatedListener(verifyAndFinish);
    const errorSub = purchaseErrorListener((err: any) => {
      setPurchasing(false);
      userInitiated.current = false;
      if (err?.code === 'user-cancelled' || err?.code === 'E_USER_CANCELLED') return;
      cbRef.current.onError(err?.message ?? 'Não foi possível concluir a compra.');
    });

    (async () => {
      try {
        await initConnection();
        const result = await fetchProducts({ skus: productIds, type: 'subs' });
        const list = (result ?? []) as any[];
        if (!alive) return;
        setProducts(Object.fromEntries(list.map(p => [p.id ?? p.productId, p])));
        setReady(true);
      } catch {
        if (alive) cbRef.current.onError('Não foi possível carregar os planos da App Store.');
      }
    })();

    return () => {
      alive = false;
      updateSub.remove();
      errorSub.remove();
      endConnection();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productIds.join('|')]);

  const buy = useCallback(async (productId: string) => {
    setPurchasing(true);
    userInitiated.current = true;
    try {
      await requestPurchase({
        request: { apple: { sku: productId } },
        type: 'subs',
      });
    } catch (e: any) {
      setPurchasing(false);
      userInitiated.current = false;
      if (e?.code !== 'user-cancelled') {
        cbRef.current.onError(e?.message ?? 'Não foi possível iniciar a compra.');
      }
    }
  }, []);

  // Botão "Restaurar compras" — obrigatório pela Apple.
  const restore = useCallback(async () => {
    setPurchasing(true);
    userInitiated.current = true;
    try {
      const purchases = await getAvailablePurchases();
      if (purchases.length === 0) {
        userInitiated.current = false;
        cbRef.current.onError('Nenhuma assinatura encontrada nesta conta da Apple.');
        return;
      }
      await verifyAndFinish(purchases[0]);
    } catch {
      userInitiated.current = false;
      cbRef.current.onError('Não foi possível restaurar suas compras.');
    } finally {
      setPurchasing(false);
    }
  }, [verifyAndFinish]);

  return { ready, products, purchasing, buy, restore };
}