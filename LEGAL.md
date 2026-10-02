# Legal notice & disclaimer

**Mempool Omega is research and educational software. It is not financial, investment, legal or tax advice.**

- The system runs in **paper-trading mode only**. It places simulated orders against public market data. It does
  not and cannot trade real funds: `Settings.live_trading` is hard-coded to `False`, and no mainnet order path exists.
- An optional adapter can mirror paper orders to the **Binance Spot Testnet** (fake money). It is disabled by
  default and refuses any non-testnet endpoint.
- **No profit is promised, implied or guaranteed.** The "100x" goals in this project refer to *infrastructure
  cost*, *experiment speed* and *adversarial awareness*, never to returns. Back-tests and paper results, especially
  on the synthetic data used for demos and red-team tests, are not indicators of future performance.
- Crypto assets are highly volatile and can go to zero. Markets are adversarial: signals that worked historically
  can be faked, crowded or arbitraged away.
- You are responsible for complying with the laws of your jurisdiction and the terms of service of every data
  provider and exchange you use (including geo-restrictions, rate limits and attribution requirements). Some venues
  (e.g. Binance, Bybit) restrict access from certain regions; this project uses each provider's *public* endpoints
  only and respects their rate limits.
- If you adapt this code to trade real money, you do so entirely at your own risk. The authors accept no liability
  for any loss.

Licence: MIT (see `pyproject.toml`). The software is provided "as is", without warranty of any kind.
