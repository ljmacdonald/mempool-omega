# Payment links (Stripe)

The repository can create Stripe payment links for any product, at any time, without anyone ever pasting a key into
a chat or a file. The key lives only in a GitHub Actions secret, and it is a **restricted** key that can do three
things: create products, create prices and create or switch off payment links. It cannot refund, move money,
read customers or change payouts. The tool refuses a full secret key (`sk_...`).

## One-time setup (about 15 minutes)

### 1. Stripe account
1. Sign up at https://dashboard.stripe.com/register.
2. You can use **test mode** straight away (top-right toggle "Test mode"). Test links take fake cards only.
3. To accept real money, click **Activate payments** and fill in the business details: legal business type
   (sole proprietor or company), EIN or SSN, address, a public website, and the bank account for payouts.

Before going live, Stripe expects your website to show: the business name, a contact email, what you sell and its
price, a **refund policy**, **terms of service** and a **privacy policy**.

### 2. Restricted key (do this in test mode first)
1. Stripe Dashboard → **Developers** → **API keys** → **Create restricted key**.
2. Name: `mempool-omega payment links`.
3. Set these three to **Write** and leave everything else on **None**:
   - **Products**
   - **Prices**
   - **Payment Links**
4. Click **Create key** and copy it (it starts with `rk_test_`). Stripe shows it only once.

### 3. Put the key in GitHub (not in a chat)
1. github.com → this repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
2. Name: `STRIPE_RESTRICTED_KEY`. Secret: the `rk_test_...` key. **Add secret**.

When test links work, repeat step 2 with Test mode **off** (the key starts with `rk_live_`) and replace the secret's
value with it.

## Making a link
- **Ask Claude**, e.g. "create a monthly payment link for Omega Pro at $19". Claude starts the job and gives
  you the link.
- **Or do it yourself:** Actions tab → **Create or switch off a payment link (Stripe)** → **Run workflow** → fill in the
  name, price in US dollars, `one_time`, `month` or `year`, optional description and after-payment page → **Run**.
  The link is in the run's summary.

Every link is recorded in `state/payments/links.json` (name, price, link, test or live). To stop a link accepting
payments, run the same job with **deactivate** and the link id (`plink_...`).

## If something goes wrong
- **The key leaks:** Stripe Dashboard → Developers → API keys → the key's **…** menu → **Delete**. It stops working at
  once. The most anyone could have done with it is create unwanted products and payment links, which you can
  archive.
- **"does not have the required permissions":** the key is missing one of the three Write permissions above.
- **Several attempts made several products:** a run that fails after creating the product leaves an unused
  product. Archive it under Products; it costs nothing.

## Not covered here
- A payment link only collects money. Letting paying customers into paid pages needs accounts and access checks,
  which is separate work.
- Selling trading ideas can count as investment advice. In the US, the SEC and state rules on investment advisers
  may apply, and a narrow exclusion exists for impersonal general publications. Get advice from a securities lawyer
  before charging anyone. Taxes on sales (sales tax and VAT) can be switched on in Stripe Tax.
