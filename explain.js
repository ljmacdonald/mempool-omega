/* Plain-English explanations for every label on the site, so a beginner never has to work out what a word or a
   number means before acting.
   - Labels (column headings, the small headings on idea cards, badges, section titles) get a dotted underline:
     click one and a card explains what it means and what to do about it.
   - Controls (buttons, switches, form fields) get a small "i" next to them, or can be explained with Explain mode
     (help menu, top right): while it's on, tapping anything explains it instead of pressing it.
   - Each page and tab shows a short "what you're looking at, what to do" tip, which the visitor can hide.
   - A searchable glossary lists every term.
   Purely presentational: it adds explanations next to the page's own elements and never changes what they do. */
(function (root) {
  "use strict";
  const PAGE = (root.OmegaShell && root.OmegaShell.page) || "main";
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: tips just reappear */ } },
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ the glossary
  // Each entry: keys (exact label text, compared in lower case without emoji, counts or a trailing colon) and/or
  // re (patterns), t = title, w = what it means, d = what to do about it. "page" limits an entry to some pages.
  const G = [
    // ---- the idea card
    { keys: ["suggested"], t: "Suggested", w: "What the model suggests: BUY means it expects the price to rise, SELL (forex and futures) means it expects it to fall.", d: "Only act on ideas graded Strong or Moderate. Ideas marked “Avoid - watch only” are shown so you can learn, not to trade." },
    { keys: ["price now", "price now (live)", "price at last update", "price at last check", "price", "last close"], t: "Price now", w: "The latest price the page has seen. Live prices update every few seconds; others at the last data refresh.", d: "Compare it with the suggested entry. If the price has already run far past the take-profit level, the idea is stale: skip it." },
    { keys: ["take profit at", "take profit"], t: "Take profit", w: "The price where you sell for a gain. It's set from how far this market usually moves in the time allowed, after fees.", d: "When the price reaches it, sell. Don't hold out for more: the tests that make the idea worth taking assume you sell here." },
    { keys: ["safety exit at", "safety exit", "stop loss", "safety exit: % below your price"], t: "Safety exit", w: "The price where you sell to stop a small loss becoming a big one (often called a stop loss).", d: "Decide before you buy that you will sell here. Most brokers let you set it as a “stop” order so it happens automatically." },
    { keys: ["take profit / safety exit"], t: "Take profit / safety exit", w: "The two prices that end the trade: sell for a gain at the first, sell to limit a loss at the second.", d: "Whichever is reached first ends the trade." },
    { keys: ["sell by", "close by", "hold for at most"], t: "Time limit", w: "Every idea has a deadline. If neither the take profit nor the safety exit has been reached by then, the edge has gone.", d: "Sell at the deadline, whatever the price. Holding longer turns a tested idea into a guess." },
    { keys: ["beats the market", "beats the others"], t: "Chance it beats the market", w: "How often ideas like this did better than simply holding the market (for crypto, Bitcoin; for stocks, the other stocks) over the same time, in past tests.", d: "Above 50% is better than a coin flip. The higher the better, but no idea is certain." },
    { keys: ["to risk $10, buy"], t: "Position size for $10 of risk", w: "How much to buy so that, if the safety exit is hit, you lose about $10 including fees.", d: "Scale it to your own comfort: to risk $5, buy half of this. Never risk money you can't afford to lose." },
    { keys: ["all costs, round trip", "costs", "typical all-in cost", "every cost, at the take profit"], t: "All costs", w: "Everything buying and selling costs you: trading fees, the gap between buy and sell prices (the spread) and slippage.", d: "The page already subtracts these from every result. Use the cheapest venue it lists." },
    { keys: ["break-even", "break-even price"], t: "Break-even", w: "The price you need to sell at just to get your money back after all costs.", d: "If the take profit is barely above break-even, the trade isn't worth the risk." },
    { keys: ["why it was picked", "why"], t: "Why it was picked", w: "The plain-English reasons behind the score: momentum, volume, how it compares with the market, and any warnings.", d: "Read the warnings (amber). A good score with a serious warning deserves extra care." },
    { keys: ["profit calculator", "real profit calculator: the money you'd actually take out", "real costs: what you actually take out"], t: "Profit calculator", w: "Type an amount and a selling price to see what you'd really take out after every cost.", d: "Try the take-profit and the safety-exit prices to see your best and worst case before buying." },
    { keys: ["i put in $", "your money $", "amount $", "paper amount $", "amount you put in ($)"], t: "Your amount", w: "The money you're thinking of putting in. It only changes the calculations on this page; nothing is bought.", d: "Type a realistic amount to see results in dollars." },
    { keys: ["and sell at $"], t: "Selling price", w: "The price you might sell at. The calculator shows the money you'd take out at that price.", d: "Try the take-profit price, then the safety-exit price." },
    { keys: ["leverage", "leverage, in plain words"], t: "Leverage", w: "Borrowing to trade a bigger amount than you have. It multiplies wins and losses alike; a small move against you can wipe out your money.", d: "Beginners should use no leverage at all. The results on this site assume none unless stated." },
    { keys: ["reward : risk"], t: "Reward : risk", w: "How much you stand to make for every $1 you could lose. 2 : 1 means the target is twice as far as the safety exit.", d: "Higher is better, but a far target is also hit less often. Check the chance shown alongside it." },
    { keys: ["chance target first", "target first"], t: "Chance the target is hit first", w: "How often, in past tests, the price reached the take profit before the safety exit.", d: "Compare it with the break-even chance shown below it: only above that does the setup make money over time." },
    { keys: ["chance it makes money", "made money", "ended in profit", "hit rate"], t: "Chance it makes money", w: "How often trades like this ended with a profit after all costs, in past tests or finished paper trades.", d: "A high hit rate isn't enough on its own: one big loss can undo many small wins. Look at the average result too." },
    { keys: ["expected, after costs"], t: "Expected result", w: "The average result of trades like this after all costs, for every $1 you put at risk. +0.20R means you'd expect to make 20 cents per $1 risked, on average.", d: "Only positive numbers are worth acting on. It's an average: any single trade can lose." },
    { keys: ["per $1 risked"], t: "Per $1 risked (R)", w: "Results measured against what you risk: +1R means you made as much as you risked; −1R means the safety exit was hit.", d: "Use it to compare ideas fairly, whatever their size." },
    { keys: ["test evidence"], t: "Test evidence", w: "How strongly past tests back this kind of setup: Held up, Promising, Unproven or Weak history.", d: "Prefer setups that have held up. Treat the rest as practice." },
    { keys: ["entry (limit order)", "entry", "entry price", "paper buy at", "bought", "price then", "price you paid ($)"], t: "Entry price", w: "The price the trade was (or would be) bought at. A limit order means you only buy if the price comes down to this level.", d: "If the price never reaches a limit entry, there is no trade. That's normal; don't chase it." },
    { keys: ["entry → exit price"], t: "Entry → exit price", w: "The price the trade was bought at, and the price it was sold at.", d: "Use it to check the result yourself." },
    { keys: ["i bought this: watch it for me", "i took this trade: watch it for me", "i placed this order: watch it for me", "watch this trade"], t: "Watch this trade for me", w: "Adds the idea to My trades on this page (in this browser only). The page then tells you when to take profit, when to get out and when time is up.", d: "Press it only after you've really bought, or to practise. Then keep this page open, or turn on alerts." },
    { keys: ["risk: low", "risk: medium", "risk: high", "risk: very high"], t: "Risk level", w: "How wild this coin's price swings are, compared with the others. Very high risk means big moves both ways, fast.", d: "Use a smaller amount on higher-risk ideas, so a safety exit costs you the same in dollars." },
    { re: [/^ranked #\d+$/], t: "Ranked", w: "Where this idea sits in today's list. Grade words (Strong, Moderate…) are switched off for now because the latest checks showed they weren't reliable, so only the order is shown.", d: "Treat these ideas as practice until grades come back on." },
    { keys: ["strong"], t: "Strong", w: "The best grade: in past results, ideas with this grade did clearly better than random picks after costs.", d: "The ideas most worth considering. Still use the safety exit and time limit." },
    { keys: ["moderate"], t: "Moderate", w: "Better than break-even after costs in past results, but by less than Strong.", d: "Consider it, with a smaller amount than a Strong idea." },
    { keys: ["weak"], t: "Weak", w: "Only a small edge, if any, after costs.", d: "Usually not worth the risk. Watch it to learn." },
    { keys: ["avoid - watch only", "avoid"], t: "Avoid - watch only", w: "Expected to lose money after costs. It's shown so you can see why it was ruled out.", d: "Don't trade it." },
    { keys: ["updates every 10 s"], t: "Live price", w: "This price refreshes every 10 seconds while the page is open.", d: "Nothing to do: just check it before you act." },
    { keys: ["scam and rug-pull checks", "passed every hard rule"], re: [/^passed \d+ scam and rug-pull checks$/, /^passed every hard rule/], t: "Scam and rug-pull checks", w: "Automatic checks for the usual tricks: tokens you can buy but not sell, hidden taxes, owners who can mint more or pull the money, and fake trading.", d: "A token must pass every hard rule to be shown. Open the list to read each check. Passing lowers the risk of a scam; it can't remove it." },
    { keys: ["manipulation checks", "market checks (news, chasing, momentum, reference coin)"], t: "Manipulation checks", w: "Checks that the move looks genuine and not like fake volume, a pump, or a move driven by one news headline.", d: "If a check shows a warning, be extra careful or skip the idea." },
    { re: [/^\d+ clean(, \d+ warnings?)?$/, /^\d+ of \d+$/], t: "Checks passed", w: "How many of the safety checks this idea passed cleanly, and how many raised a warning.", d: "Open the list to see which check warned, and why." },

    // ---- settings at the top
    { keys: ["trading speed", "seg:quick", "seg:short", "seg:day", "seg:few days", "seg:today"], t: "Trading speed", w: "How long you want to hold a trade. Quick ideas end within an hour, Short within 4 hours, Day within 24 hours (stocks and forex: Today or a few days).", d: "Pick the one that matches how often you can check the page. If you can't check back within the hour, don't use Quick." },
    { keys: ["refresh", "refresh now"], t: "Refresh", w: "How often the page fetches new prices and re-ranks the ideas.", d: "Every 5 minutes is fine for most people. Press Refresh now before you act on an idea." },
    { keys: ["currency fee"], t: "Currency fee", w: "If your account isn't in US dollars, your broker charges to convert money each way. It's often the biggest cost on US stocks.", d: "Pick your broker's rate so the results match what you'd really get." },
    { keys: ["network", "all", "solana", "bsc", "bnb chain", "ethereum", "robinhood", "robinhood chain", "chain"], t: "Network", w: "The blockchain a token lives on. Each has its own exchanges, fees and wallet.", d: "Pick the network your wallet uses, or All to compare." },
    { keys: ["safety level", "standard or higher risk", "seg:standard", "seg:higher risk"], t: "Safety level", w: "Standard shows only tokens with at least $500k of trading money that are 14+ days old. Higher risk lowers that to $100k and 5 days: more ideas, more danger.", d: "Beginners should stay on Standard." },
    { keys: ["which stocks", "large stocks", "high volatility", "high-volatility stocks"], t: "Which stocks", w: "Large stocks are the most-traded US companies (calmer). High volatility stocks move much more, both ways.", d: "Start with Large stocks." },
    { keys: ["which pairs", "majors & crosses", "major pairs", "exotics", "gold & silver", "crosses"], t: "Which currencies", w: "Majors are the most-traded pairs with the lowest costs. Crosses don't include the US dollar. Exotics are costly and managed by central banks, so they're information only.", d: "Start with the majors." },
    { keys: ["live prices (optional): add your free finnhub key"], t: "Live stock prices", w: "Optional. With a free key from finnhub.io the page shows live prices instead of prices delayed by a few minutes. The key stays in this browser.", d: "You can skip this; the ideas work without it." },
    { keys: ["alerts", "alerts off", "alerts on"], t: "Alerts", w: "Pop-up messages with a sound when a strong idea appears, or when one of your trades needs action. Clicking an alert opens the exact idea.", d: "Turn them on so you don't have to keep watching the page. They work while any page of this site is open in a tab." },
    { keys: ["day or night colours"], t: "Colours", w: "Light, dark, or A (automatic: follows your device).", d: "Pick whichever is easiest to read." },

    // ---- tabs and sections
    { keys: ["top 5 ideas", "top 10 ideas", "top 5 dex ideas", "trade ideas now", "ideas now"], t: "Trade ideas", w: "The best-scoring ideas right now, after costs and safety checks, each with where to sell for a profit, where to get out and a time limit.", d: "Start here. Read the top idea's card, check the grade and warnings, then decide." },
    { keys: ["setups now"], t: "Setups now", w: "ICT setups the code has found right now, each with an exact entry price, target and safety exit.", d: "Only place the order if the grade and test evidence are good. A limit order may never fill, and that's fine." },
    { keys: ["biggest movers"], t: "Biggest movers", w: "Today's biggest price moves, and why each one is or isn't suggested. Usually it already jumped (often a trap), trades too little, or other ideas score higher.", d: "Use it to resist chasing: if a mover isn't suggested, the reason is shown." },
    { keys: ["my trades", "my dex trades", "my stock trades", "my forex trades", "my ict trades"], t: "My trades", w: "Trades you're following. The page checks the price every 30 seconds and tells you, in plain words, what to do now. Saved in this browser only.", d: "Keep this tab open while you hold a trade, or turn on alerts." },
    { keys: ["track record", "track record of the ideas", "live record"], t: "Track record", w: "Every past idea, followed to its end, wins and losses, after costs, compared with random picks.", d: "Check here that the page is actually beating random before trusting its ideas." },
    { keys: ["self-improvement", "what the system changed about itself", "trying to improve the rules"], t: "Self-improvement", w: "The model re-tests itself on recent results and adjusts its settings, keeping a change only if it also works on data it didn't learn from.", d: "Nothing to do: it shows what changed and why." },
    { keys: ["practice account"], t: "Practice account", w: "A pretend $100,000 account that automatically takes the page's ideas, so you can see how following them would really have gone.", d: "Watch it for a few weeks before trusting the ideas with real money." },
    { keys: ["how it works", "how to use this page", "what this page does", "why this page exists", "honest limits", "what the numbers mean", "simple rules that protect you"], t: "How it works", w: "Plain-English notes on how this page builds its ideas, what it can't do, and the rules that protect you.", d: "Worth reading once before you act on anything." },
    { keys: ["news & fixes", "coming up: high-impact announcements", "next rate-fixing windows (no entries or exits inside)"], t: "News and fixing times", w: "Big economic announcements and the daily rate-fixing windows, when prices can jump in seconds.", d: "Don't enter or exit trades inside these windows." },
    { keys: ["how each grade actually turned out", "how each grade actually turned out (all strategies)"], re: [/: how each grade actually turned out$/], t: "How each grade turned out", w: "For each grade the page gave (Strong, Moderate…), how those ideas really ended, after costs, against random picks.", d: "If Strong isn't clearly beating random here, don't rely on the grades." },
    { keys: ["latest checked ideas", "latest finished trades", "latest finished setups (all markets)", "finished", "closed"], t: "Finished ideas", w: "Ideas that have ended (take profit, safety exit or time limit), with the real result after costs.", d: "Look at the losers as well as the winners. That's what real trading looks like." },
    { keys: ["scoreboard"], t: "Scoreboard", w: "Each famous strategy re-tested on many years of prices, after costs, against random trades, and whether it held up after it was published.", d: "Only strategies marked Held up are used for trade ideas." },
    { keys: ["exit signals"], t: "Exit signals", w: "Trades the strategies entered that have just been told to sell.", d: "If you followed one of these, sell now." },
    { keys: ["don't be tempted"], t: "Don't be tempted", w: "Rules firing right now that failed the tests. They look like opportunities but lost money after costs.", d: "Don't trade these." },
    { keys: ["listed in the last 45 days", "newer listings"], t: "Recent listings", w: "Coins added to big exchanges in the last 45 days, and how each has done since its first price.", d: "Most new listings fall after the first days. Only act on the ideas at the top." },
    { keys: ["coming soon"], t: "Coming soon", w: "Coins announced for listing that haven't started trading yet.", d: "Nothing to do yet. The first hours are the most dangerous; wait for a tested rule." },
    { keys: ["what really happens after a listing: every rule tested"], t: "Every listing rule tested", w: "Every simple rule for trading new listings (buy at once, buy the dip, short the first day…) tested on past listings after costs.", d: "Only rules that held up produce ideas." },
    { keys: ["passed the checks just now"], t: "Passed the checks", w: "Brand-new tokens that just passed every scam and rug-pull check. The page paper-buys them automatically to see whether sniping pays.", d: "Paper only: watch the results before believing in sniping." },
    { keys: ["rejected just now, and why", "rejected ones", "why it was rejected", "tokens checked and rejected this hour"], t: "Rejected tokens", w: "New tokens that failed at least one check, and which check.", d: "Nothing to do. It shows how many new tokens are traps." },
    { keys: ["paper snipes in progress"], t: "Paper snipes in progress", w: "Pretend buys of new tokens that are still open, with their current result after costs.", d: "Watch them; nothing is bought with real money." },
    { keys: ["results so far: do the checks make sniping pay?"], t: "Do the checks make sniping pay?", w: "Compares tokens that passed the checks with those that were rejected, after costs, to see whether the checks really help.", d: "Wait for at least 30 finished snipes before drawing conclusions." },
    { keys: ["our speed against the bots", "our typical delay", "slowest 10%", "professional snipers"], t: "Speed against the bots", w: "How long after a pool opens this page sees it, compared with professional sniper bots that buy within seconds.", d: "It shows why a slower buyer is at a disadvantage on brand-new tokens." },
    { keys: ["paper mirror: copying the whales' whole positions", "best whales, copied", "random whales, copied", "just holding btc"], t: "Paper mirror", w: "A pretend account that copies everything last month's best big traders hold, every 15 minutes, after costs and funding, compared with copying random big traders or just holding Bitcoin.", d: "Wait for the verdict after 30 days. Until then this is research, not a signal." },
    { keys: ["what the whales hold now", "what the mirror holds now", "every position"], t: "What the whales hold", w: "Which coins the followed whales are buying (long) or betting against (short) right now, and how much.", d: "Many whales on the same side is interesting, not a guarantee." },
    { keys: ["just changed"], t: "Just changed", w: "Positions the whales opened, closed, grew or cut since the last check.", d: "Information only, until the paper mirror has proved itself." },
    { keys: ["the whales we follow this month", "whale"], t: "The whales we follow", w: "The 20 big traders with the best results last month (by profit on their account), picked again every night.", d: "Nothing to do: the mirror copies them automatically." },
    { keys: ["why whales, and why whole positions"], t: "Why whole positions", w: "The research behind the page: copying single trades failed the tests, while last month's best traders did keep doing better the next month.", d: "Read it to understand what's being tested." },
    { keys: ["what ict is", "ict in plain words", "how to read a setup", "the confluence checklist", "ict checklist", "ict setup now?"], t: "ICT", w: "Inner Circle Trader: a popular style that looks for price returning to areas where big orders were placed. The page codes the rules exactly and tests them.", d: "Use the checklist on each setup: more ticks, more agreement." },

    // ---- results tables
    { keys: ["ideas checked", "th:ideas", "ideas", "trades", "trades tested", "filled", "th:setups"], t: "Number of trades", w: "How many past ideas or trades the result is based on.", d: "Fewer than 30 is too few to trust: wait for more." },
    { keys: ["average per idea", "average after fees", "average after costs", "average per trade", "average per trade after costs", "result after costs", "result after all costs", "result", "since the buy, after costs", "profit so far", "so far", "month so far"], t: "Result after costs", w: "The gain or loss after every fee and cost, in percent of the money put in.", d: "This is the number that matters. Ignore results shown before costs anywhere else." },
    { keys: ["random pick average", "random pick", "random entry", "random entries", "better than random by"], t: "Random comparison", w: "What random trades in the same market, at the same times and with the same exits, made. It's the honest yardstick: anyone can make money in a rising market.", d: "Only trust ideas that beat random by a clear margin over many trades." },
    { keys: ["beating random", "not beating random"], t: "Beating random?", w: "Whether these ideas did better than random trades at the same times, after costs.", d: "Not beating random means the ideas haven't proved themselves yet: treat them as practice." },
    { keys: ["on $100", "with $100 on paper"], re: [/^with \$100 on paper/], t: "On $100", w: "What $100 would have become after all costs.", d: "A quick way to picture the result in money." },
    { keys: ["grade given"], t: "Grade given", w: "The grade the page gave when the idea was suggested.", d: "Compare grades: Strong should do better than Moderate, and both better than random." },
    { keys: ["what happened", "why it closed"], t: "What happened", w: "How the trade ended: take profit, safety exit or time limit.", d: "Nothing to do: it's the record." },
    { keys: ["suggested (utc)", "entered", "listed", "pool opened", "trading opens", "when (your time)"], t: "Time", w: "When it happened. UTC is world time (London in winter). Times marked “your time” are in your own time zone.", d: "Nothing to do." },
    { keys: ["score"], t: "Score", w: "A score out of 10. 5 is break-even after costs; higher means the model expects a better result.", d: "Above about 6.5 is graded Strong." },
    { keys: ["verdict", "held up", "not proven", "not proven yet", "not proven in tests", "failed"], t: "Verdict", w: "Whether the idea passed the honest test: enough trades, a positive result after costs, clearly beating random, and still working on data the rules never saw.", d: "Only rely on things marked Held up. Not proven means wait; Failed means don't use it." },
    { keys: ["fill rate"], t: "Fill rate", w: "How often the limit order's price was actually reached, so the trade happened.", d: "A low fill rate is normal for limit orders." },
    { keys: ["choosing period: result vs random", "unseen period: result vs random", "version of the rules", "chosen", "used for grades", "experiment"], t: "Honest testing", w: "Rules are chosen on one period of data, then checked on a later period they never saw. Only the unseen-period result counts.", d: "Trust a version only if it beat random on the unseen period too." },
    { keys: ["does each checklist item help?", "with it", "without it", "item"], t: "Does each item help?", w: "Compares setups with and without each checklist item, to see whether that item really improves results.", d: "Items that don't help are not used to grade setups." },
    { keys: ["traded (24 h)", "traded", "traded (last day)", "24 h change"], t: "Trading activity", w: "How much money changed hands, or how much the price moved, in the last 24 hours.", d: "Very low trading means it's hard to sell at a fair price: be careful." },
    { keys: ["why it is or isn't suggested"], t: "Why it is or isn't suggested", w: "The reason a big mover is, or isn't, among the ideas.", d: "If it isn't suggested, don't chase it." },
    { keys: ["in the current top 5"], t: "In the current top 5", w: "This mover is also one of today's trade ideas.", d: "Open the Top 5 ideas tab to see its card." },
    { keys: ["speed", "style", "usually held", "average time in a trade"], t: "How long trades last", w: "How long these trades are usually held, from minutes to days.", d: "Choose ideas whose length matches how often you can check." },
    { keys: ["average win / loss"], t: "Average win / loss", w: "The average size of the winning trades and of the losing ones.", d: "Wins should be at least as big as losses unless the hit rate is high." },
    { keys: ["strategy", "rule", "rule firing now", "strategy entered"], t: "Strategy or rule", w: "The named set of trading rules that produced the trade.", d: "Check its verdict on the scoreboard before following it." },
    { keys: ["how it did in the test"], t: "How it did in the test", w: "The tested result of this rule after costs.", d: "A negative result is why you shouldn't trade it." },
    { keys: ["side", "long", "short", "buy", "sell"], t: "Side", w: "Long or BUY: profits if the price rises. Short or SELL: profits if the price falls.", d: "Shorting needs a broker or exchange that allows it, and can lose more than you put in if leverage is used." },
    { keys: ["whales long / short", "long $", "short $", "leaning"], t: "Whales long / short", w: "How many followed whales are buying (long) versus betting against (short) the coin, and the dollar size of each side.", d: "A strong lean is information, not a signal, until the mirror's verdict is in." },
    { keys: ["size, per $1 of paper money", "size", "positions"], t: "Size", w: "How big the position is. For the mirror, per $1 of paper money: 0.30 means 30 cents of every dollar is in this coin.", d: "Nothing to do." },
    { keys: ["account", "last month when picked"], t: "Whale's account", w: "The money in the whale's trading account, and its profit last month as a share of that money.", d: "Nothing to do." },
    { keys: ["days of results"], t: "Days of results", w: "How many days the paper mirror has run. A verdict needs at least 30.", d: "Come back after 30 days." },
    { keys: ["exchange", "pool", "pool fee", "money out at take profit", "where to buy it cheapest, and the real token address"], t: "Where to trade", w: "The exchanges or pools you could buy on, with their fees and what you'd take out.", d: "Use the cheapest, and always copy the token address from here to avoid fake copycat tokens." },
    { keys: ["real money in pool", "pool money since the buy", "being pulled", "biggest pool"], t: "Money in the pool", w: "The real money (SOL, ETH, BNB or stablecoins) traders can sell into. If it drops fast, the owners may be pulling it: a rug pull.", d: "If “being pulled” appears, the token is dangerous: get out." },
    { keys: ["rugged: passed / rejected"], t: "Rugged", w: "How many tokens later collapsed, among those that passed versus those that were rejected.", d: "Good checks mean far fewer rugs among the passed tokens." },
    { keys: ["exit plan"], t: "Exit plan", w: "The paper sniper's rules for selling: take profit, safety exit and time limit.", d: "Nothing to do: it shows the rules being tested." },
    { keys: ["stage", "held", "still open", "right now"], t: "Status", w: "Where the trade or listing stands now: open, finished, or in which stage after listing.", d: "Nothing to do." },
    { keys: ["since first price", "from its high"], t: "Since listing", w: "How far the price has moved since its first price on the exchange, and how far below its highest price it now is.", d: "Big drops from the high are common after listings." },
    { keys: ["data trust"], t: "Data trust", w: "From 0 to 1: how genuine today's market signals look. Low means many moves look like manipulation or fake volume.", d: "When it's low, trade smaller or not at all." },
    { keys: ["emergency stop"], t: "Emergency stop", w: "If the practice account loses too much too fast, everything is closed and new trades pause.", d: "If it's On, take that as a warning that today's market is hostile." },
    { keys: ["practice balance", "open practice trades"], t: "Practice balance", w: "The pretend account's money and its open trades.", d: "Nothing to do: watch how it does over weeks." },
    { re: [/^(quick|short|day) speed$/], t: "Speed settings", w: "The settings the model is currently using for this trading speed, after its last self-test.", d: "Nothing to do." },
    { keys: ["coin", "token", "stock", "pair", "market", "currency", "#", "what", "type", "checks"], t: "Name", w: "What is being traded: a coin, a token, a stock, a currency pair or a market.", d: "Click the row or card for details." },
    { keys: ["add a trade i made myself"], t: "Add your own trade", w: "If you bought something yourself, enter it here and the page will watch it like its own ideas.", d: "Fill in what you bought, the price and when, then press Watch this trade." },
    { keys: ["move my trades to another device", "copy", "load trades"], t: "Move trades to another device", w: "Your trades live in this browser only. Copy them here, then paste them with Load trades on your other device.", d: "Use it if you switch between a phone and a computer." },
    { keys: ["turn on alerts for my trades"], t: "Alerts for my trades", w: "Pop-ups with sound when one of your trades reaches its take profit, its safety exit or its time limit.", d: "Turn it on if you hold a trade and won't be watching the page." },
    { keys: ["remove"], t: "Remove", w: "Stops following this trade on the page. It doesn't sell anything.", d: "Use it after you've sold." },
    { keys: ["save"], t: "Save", w: "Saves the setting in this browser.", d: "" },
    { keys: ["how long will you hold?", "minutes ago you bought", "coin (for example sol)"], t: "Trade details", w: "What you bought, how long you plan to hold it, and when you bought it, so the page can work out the time limit.", d: "Fill it in as accurately as you can." },
    { keys: ["?", "help"], t: "Help", w: "Take the tour, open the glossary, turn on Explain mode or show the page tips.", d: "" },
  ];
  G.push({ keys: ["which market", "crypto", "forex & gold", "us index futures", "index · 1-hour"], t: "Which market", w: "ICT setups for crypto, for forex and gold, or for US index futures (S&P 500, Nasdaq, Dow) on 1-hour charts.", d: "Pick the market you can actually trade with your broker." });
G.push(
    { keys: ["promising", "unproven", "weak history", "held up in tests", "held up in paper tests"], t: "Test evidence", w: "How strongly past tests back this kind of setup. Held up: a clear edge after costs over enough trades. Promising: positive but not yet certain. Unproven: too few past trades. Weak history: it didn't make money.", d: "Prefer Held up. Treat Promising and Unproven as practice; avoid Weak history." },
    { re: [/^profit calculator/], t: "Profit calculator", w: "Type an amount and a selling price to see what you'd really take out after every cost (spread, fees, currency conversion).", d: "Try the take-profit and the safety-exit prices to see your best and worst case before buying." },
    { re: [/^where to buy/], t: "Where to buy", w: "Where this can be bought most cheaply, with what you'd actually get after fees.", d: "Use the cheapest venue shown. For tokens, copy the address from here to avoid fake copies." },
    { re: [/^fake-signal check/, /^\d+ of \d+ checks passed$/, /^fake-signal checks/], t: "Fake-signal checks", w: "Checks that the move is genuine: real buyers and sellers, no fake volume, no one-sided order book, no sudden pump.", d: "All passed is best. If some failed, open the list to see which, and be more careful." },
    { re: [/^tokens checked and rejected/], t: "Rejected tokens", w: "New tokens checked in the last hour that failed at least one scam or safety check, and which one.", d: "Nothing to do. It shows how many new tokens are traps." },
    { re: [/^(uniswap|pancakeswap|raydium|pumpswap|aerodrome|meteora|orca|sushiswap|camelot|curve|balancer|traderjoe|fluxbeam)/], t: "Exchange (DEX)", w: "The decentralised exchange (and its version) where this token's main pool trades.", d: "Buy on this exchange, using the token address shown on the card." },
    { keys: ["each market"], t: "Market", w: "Which market the strategy is tested on, for example the S&P 500 or Bitcoin.", d: "Use the strategy only on the markets where it held up." },
    { keys: ["2-day rsi"], t: "2-day RSI", w: "A measure from 0 to 100 of how much the price fell or rose in the last 2 days. Below 10 means it has dropped sharply: a possible dip to buy.", d: "The strategy buys when it's very low and sells when it bounces." },
    { keys: ["above 200-day average?"], t: "Above the 200-day average?", w: "Whether the price is above its average of the last 200 days: a simple sign that the long-term trend is up.", d: "The dip-buying strategy only buys when this is Yes." },
    { keys: ["buy signal at the close?"], t: "Buy signal at the close?", w: "Whether all the strategy's buy rules are met at today's closing price.", d: "If Yes, the strategy would buy at the close." },
    { re: [/^connors rsi/], t: "Connors RSI(2) dip buying", w: "Buys sharp short-term drops in a market that is in a long-term uptrend, and sells on the bounce. Published by Larry Connors.", d: "Check its verdict on the Scoreboard before following it." },
    { re: [/^turtle breakout/], t: "Turtle breakout", w: "Buys when the price breaks above its highest level of the last 20 days, from the famous 1980s Turtle traders.", d: "Check its verdict on the Scoreboard before following it." },
    { re: [/^golden cross/], t: "Golden cross", w: "Buys when the 50-day average price rises above the 200-day average, and sells when it falls back below.", d: "Check its verdict on the Scoreboard before following it." },
    { re: [/^opening range breakout/], t: "Opening range breakout", w: "Buys or sells when the price breaks out of its range in the first minutes after the market opens.", d: "Check its verdict on the Scoreboard before following it." },
    { re: [/^freqtrade/], t: "Freqtrade strategy", w: "A popular strategy from the free, open-source freqtrade trading bot, re-tested here honestly after costs.", d: "Check its verdict on the Scoreboard before following it." },
    { keys: ["real money"], t: "Real money", w: "What would have happened with real money, after realistic costs for a slower buyer like this page.", d: "This is the number to believe, not the paper result." },
    { keys: ["passed", "rejected", "passed: snipes"], t: "Passed or rejected", w: "Whether the token passed every scam and rug-pull check (and was paper-bought) or was rejected.", d: "Compare the two groups: the checks only help if passed tokens do clearly better." },
    { keys: ["market signals"], t: "Market signals", w: "Three standard signals for the coin. Trend: is the price above its 50-day average? Momentum: is it higher than 2 weeks ago? Crowding: are unusually many traders paying to bet one way (high funding)? Crowding counts against the crowd.", d: "Context only. In a 2-year test these signals called the direction right about half the time." },
    { keys: ["outlook"], t: "Outlook (not proven)", w: "The three signals added up: Up when at least two point up, Down when at least two point down, otherwise no clear lean. It also says whether that agrees with the whales. Every call is checked 7 days later after costs.", d: "Don't trade on it while it says Not proven: in testing it was right about 50% of the time, a coin flip. It changes to Held up only after a month of live calls that clearly beat that." },
    { keys: ["picks right now", "fits the rule"], t: "Prediction-market picks", w: "Markets where the favourite costs 55-70¢, ends in 1-7 days and trades enough to buy at a fair price. In our research these favourites won more often than their price said, but on few trades.", d: "Information only until 50 paper trades have settled. A contract pays $1 if right and nothing if wrong." },
    { keys: ["pays if right"], t: "Pays if right", w: "What you'd make per $1 if the outcome you buy happens: a contract costs its price plus a fee and pays $1.", d: "Remember the other side: if it doesn't happen, you lose everything you put in." },
    { keys: ["th:buy"], t: "YES or NO", w: "Every question has two sides. YES pays $1 if it happens; NO pays $1 if it doesn't. Their prices add up to about $1.", d: "Read the question carefully: buying NO on 'Las Vegas wins' is a bet that Las Vegas does not win." },
    { keys: ["holding big coins"], t: "Holding big coins", w: "What simply holding Bitcoin and Ether over the same days made. It's the yardstick for the listing rules.", d: "A listing rule is only worth using if it beats this after costs." },
  );
  const norm = (s) => String(s || "").replace(/[\u{1F300}-\u{1FAFF}☀-➿️]/gu, "").replace(/\s*\(\d+\)\s*$/, "").replace(/[:·]\s*$/, "").replace(/\s+/g, " ").trim().toLowerCase();
  const BY = new Map();
  for (const e of G) for (const k of e.keys || []) if (!BY.has(k)) BY.set(k, e);
  function lookup(text, ctx) {
    const n = norm(text);
    if (!n || n.length > 90) return null;
    if (ctx && BY.has(`${ctx}:${n}`)) return BY.get(`${ctx}:${n}`);
    if (BY.has(n)) return BY.get(n);
    for (const e of G) if (e.re && e.re.some((r) => r.test(n))) return e;
    return null;
  }

  // ------------------------------------------------------------------ page and tab tips
  const PAGES = {
    main: { steps: ["Pick how long you can hold a trade: Quick, Short or Day.", "Open the top idea and check its grade and warnings.", "If you buy, press “I bought this” and follow it in My trades."] },
    small: { steps: ["Pick how long you can hold a trade: Quick, Short or Day.", "Small coins swing hard: use a smaller amount than usual.", "If you buy, press “I bought this” and follow it in My trades."] },
    dex: { steps: ["Choose your network and keep the safety level on Standard.", "Open the top idea, read the scam checks and copy the real token address.", "If you buy, press “I bought this” and follow it in My DEX trades."] },
    listings: { steps: ["Look at Trade ideas now: only tested rules produce ideas.", "Check Don't be tempted: rules that look good but failed.", "New listings are risky: use a small amount and the time limit."] },
    sniper: { steps: ["This page never trades: it paper-buys new tokens that pass the checks.", "Watch Results so far to see whether sniping really pays.", "Don't snipe with real money until the verdict says Held up."] },
    stocks: { steps: ["Pick Large stocks to start, and the speed: Today or Few days.", "Ideas appear during US market hours (9:30–16:00 New York).", "If you buy, press “I bought this” and follow it in My trades."] },
    fx: { steps: ["Start with the major pairs and the Today speed.", "Check News & fixes: don't trade inside the listed windows.", "If you trade, press “I took this trade” and follow it in My trades."] },
    ict: { steps: ["Each setup has an exact limit-order entry, target and safety exit.", "Check its grade, test evidence and checklist ticks.", "If you place the order, press “I placed this order” and follow it."] },
    lab: { steps: ["Check the Scoreboard: only strategies marked Held up produce ideas.", "Trade ideas now lists what those strategies say today.", "Watch Exit signals if you follow one of the trades."] },
    predict: { steps: ["Paper only: the page tests one rule on Kalshi and Polymarket markets, at real prices and fees.", "Picks right now are favourites priced 55-70¢ that end in 1-7 days.", "Wait for the verdict after 50 settled trades before trusting it."] },
    whales: { steps: ["The page copies last month's best big traders on paper.", "Wait for the verdict after 30 days before trusting it.", "Information only for now: no alerts until it holds up."] },
  };
  const TABS = {
    ideas: { w: "The best ideas right now, after costs and safety checks.", d: "Read the top card: grade, take profit, safety exit, time limit. Only act on Strong or Moderate." },
    setups: { w: "ICT setups found right now, with exact entry, target and safety exit.", d: "Place the limit order only if the grade and test evidence are good." },
    movers: { w: "Today's biggest moves, and why each is or isn't suggested.", d: "Don't chase a mover that isn't suggested: the reason is shown." },
    trades: { w: "The trades you're following, checked every 30 seconds.", d: "Do what the card says: hold, take profit or get out. Keep the page open or turn on alerts." },
    record: { w: "Every past idea followed to its end, after costs, against random picks.", d: "Trust the ideas only if they beat random over many trades." },
    improve: { w: "What the model changed about itself after re-testing on recent results.", d: "Nothing to do: it's the record of its adjustments." },
    account: { w: "A pretend $100,000 account that follows the ideas automatically.", d: "Watch it for a few weeks before using real money." },
    news: { w: "Upcoming announcements and the daily rate-fixing windows.", d: "Don't enter or exit trades inside these windows." },
    guide: { w: "How the page works and its honest limits.", d: "Read it once before acting on anything." },
  };

  // ------------------------------------------------------------------ the explanation card
  let pop = null; let popFor = null;
  function closePop() { if (pop) { pop.remove(); pop = null; popFor = null; } }
  function show(e, anchor) {
    closePop();
    pop = document.createElement("div"); pop.className = "xp-pop"; pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", e.t);
    pop.innerHTML = `<button class="xp-x" type="button" aria-label="Close">×</button><b class="xp-t">${esc(e.t)}</b><p>${esc(e.w)}</p>${e.d ? `<p class="xp-do"><span>What to do</span>${esc(e.d)}</p>` : ""}<button type="button" class="xp-more" data-glossary>All terms</button>`;
    document.body.appendChild(pop); popFor = anchor;
    const r = anchor.getBoundingClientRect(); const w = pop.offsetWidth; const h = pop.offsetHeight;
    if (innerWidth > 640) {
      let x = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
      let y = r.bottom + 8; if (y + h > innerHeight - 8) y = Math.max(8, r.top - h - 8);
      pop.style.left = `${x + scrollX}px`; pop.style.top = `${y + scrollY}px`;
    } else pop.classList.add("sheet");
    pop.querySelector(".xp-x").focus({ preventScroll: true });
  }
  document.addEventListener("click", (ev) => {
    if (pop && ev.target.closest(".xp-x")) { closePop(); return; }
    if (ev.target.closest("[data-glossary]")) { ev.preventDefault(); closePop(); glossary(); return; }
    if (pop && !pop.contains(ev.target) && !ev.target.closest("[data-xp]")) closePop();
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { closePop(); const g = document.querySelector(".xp-gloss"); if (g) g.remove(); } });
  addEventListener("resize", closePop);

  // ------------------------------------------------------------------ finding labels to explain
  const PLAIN = ".k, th, h2, h3, span.pill, dt, .stat .k, legend";
  const CTRL = "label, summary, .seg[aria-label]";
  function textOf(el) {
    if (el.matches(".seg, [data-theme-switch]")) return el.getAttribute("aria-label") || "";
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ").trim();
    return own || (el.firstElementChild && !el.matches("summary,label") ? "" : el.textContent);
  }
  function context(el) { return el.closest(".seg") ? "seg" : el.matches("th") ? "th" : ""; }
  function annotate(scope) {
    for (const el of scope.querySelectorAll(PLAIN)) {
      if (el.dataset.xp || el.closest(".xp-pop,.xp-gloss,.ob-backdrop,.gs,.oa-panel,.oa-toast,.appbar")) continue;
      if (el.matches("h2") && el.closest("article.card .card-head, .idea")) continue;
      const e = lookup(textOf(el), context(el));
      el.dataset.xp = e ? "1" : "0";
      if (!e) continue;
      el.classList.add("xp-l"); el.tabIndex = 0; el.setAttribute("role", "button");
      el.setAttribute("aria-label", `${norm(textOf(el)) || e.t}: what is this?`);
    }
    for (const el of scope.querySelectorAll(CTRL)) {
      if (el.dataset.xp || el.closest(".xp-pop,.xp-gloss,.ob-backdrop,.gs,.oa-panel,.oa-toast,nav.sites")) continue;
      const e = lookup(textOf(el), context(el));
      el.dataset.xp = e ? "i" : "0";
      if (!e) continue;
      const i = document.createElement("span"); i.className = "xp-i"; i.dataset.xp = "icon"; i.tabIndex = 0; i.setAttribute("role", "button");
      i.setAttribute("aria-label", `What is ${e.t}?`); i.textContent = "i"; i._xp = e;
      if (el.matches("summary, .seg")) el.appendChild(i); else if (el.matches("label") && el.querySelector("input,select")) el.insertBefore(i, el.querySelector("input,select")); else el.after(i);
    }
  }
  function entryFor(el) {
    if (el._xp) return el._xp;
    if (el.matches("a[data-site]")) { const p = (root.OmegaShell && root.OmegaShell.pages || {})[el.dataset.site]; return p ? { t: p.name, w: p.blurb, d: p.action || "Open it from the menu." } : null; }
    if (el.matches("nav.tabs button")) { const k = el.dataset.tab; const tip = TABS[k]; const g = lookup(el.firstChild ? el.firstChild.textContent : el.textContent); return tip ? { t: g ? g.t : el.textContent.trim(), w: g ? g.w : tip.w, d: tip.d } : g; }
    if (el.matches(".oa-bell")) return lookup("alerts");
    return lookup(textOf(el), context(el));
  }
  // labels: click to explain; the small "i": click to explain without pressing the control it sits in
  document.addEventListener("click", (ev) => {
    if (explainMode) return;
    const t = ev.target.closest(".xp-l, .xp-i");
    if (!t) return;
    ev.preventDefault(); ev.stopPropagation();
    if (popFor === t) { closePop(); return; }
    const e = t._xp || entryFor(t); if (e) show(e, t);
  }, true);
  document.addEventListener("keydown", (ev) => {
    if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches && ev.target.matches(".xp-l, .xp-i")) { ev.preventDefault(); ev.target.click(); }
  });

  // ------------------------------------------------------------------ Explain mode: tap anything to learn what it does
  let explainMode = false;
  function setExplain(on) {
    explainMode = on; document.documentElement.classList.toggle("xp-mode", on);
    let bar = document.querySelector(".xp-bar");
    if (on && !bar) {
      bar = document.createElement("div"); bar.className = "xp-bar"; bar.setAttribute("role", "status");
      bar.innerHTML = `<span><b>Explain mode is on.</b> Tap any button, tab, label or number to see what it does. Nothing gets pressed.</span><button type="button" class="btn" data-xp-off>Done</button>`;
      document.body.appendChild(bar);
    } else if (!on && bar) bar.remove();
    if (!on) closePop();
  }
  document.addEventListener("click", (ev) => {
    if (!explainMode) return;
    if (ev.target.closest("[data-xp-off]")) { ev.preventDefault(); ev.stopPropagation(); setExplain(false); return; }
    if (ev.target.closest(".xp-pop, .xp-bar, .xp-gloss, .xp-help-menu")) return;
    const el = ev.target.closest(".xp-l, .xp-i, nav.tabs button, a[data-site], button, summary, label, .oa-bell, th, .k, .pill, h2, h3, .seg[aria-label], [data-theme-switch]");
    ev.preventDefault(); ev.stopPropagation();
    if (!el) return;
    let e = entryFor(el);
    if (!e && el.closest(".seg[aria-label]")) e = entryFor(el.closest(".seg[aria-label]"));
    if (!e && el.closest("[data-theme-switch]")) e = entryFor(el.closest("[data-theme-switch]"));
    show(e || { t: (el.textContent || "This").trim().slice(0, 40) || "This", w: "No separate explanation for this one: it does what its label says.", d: "Open the glossary for every term." }, el);
  }, true);

  // ------------------------------------------------------------------ glossary
  function glossary() {
    const seen = new Set(); const rows = [];
    for (const e of G) { if (seen.has(e.t)) continue; seen.add(e.t); rows.push(e); }
    rows.sort((a, b) => a.t.localeCompare(b.t));
    const bd = document.createElement("div"); bd.className = "xp-gloss ob-backdrop"; bd.setAttribute("role", "dialog"); bd.setAttribute("aria-modal", "true"); bd.setAttribute("aria-label", "Glossary");
    bd.innerHTML = `<div class="ob-modal xp-gm"><header><h2>Every term, in plain English</h2><button type="button" class="xp-x" data-close aria-label="Close">×</button></header>
      <input type="search" class="xp-q" placeholder="Search, for example “safety exit”" aria-label="Search the glossary">
      <dl class="xp-list">${rows.map((e) => `<div data-k="${esc((e.t + " " + (e.keys || []).join(" ")).toLowerCase())}"><dt>${esc(e.t)}</dt><dd>${esc(e.w)}${e.d ? `<span class="xp-do"><span>What to do</span>${esc(e.d)}</span>` : ""}</dd></div>`).join("")}</dl></div>`;
    bd.addEventListener("click", (ev) => { if (ev.target === bd || ev.target.closest("[data-close]")) bd.remove(); });
    bd.querySelector(".xp-q").addEventListener("input", (ev) => { const q = ev.target.value.trim().toLowerCase(); for (const r of bd.querySelectorAll(".xp-list > div")) r.hidden = q && !r.dataset.k.includes(q); });
    document.body.appendChild(bd); bd.querySelector(".xp-q").focus();
  }

  // ------------------------------------------------------------------ tips: what you're looking at, what to do
  const tipsOff = () => !!store.get("omega.xp.off", false);
  const hidden = () => store.get("omega.xp.hidden", {});
  function hideTip(key) { const h = hidden(); h[key] = true; store.set("omega.xp.hidden", h); }
  function pageTip() {
    const p = PAGES[PAGE]; const hero = document.querySelector(".page-hero");
    const old = document.querySelector(".xp-steps"); if (old) old.remove();
    if (!p || !hero || tipsOff() || hidden()[`page:${PAGE}`]) return;
    const box = document.createElement("section"); box.className = "xp-steps"; box.setAttribute("aria-label", "How to use this page");
    box.innerHTML = `<div class="xp-steps-h"><b>How to use this page</b><button type="button" class="xp-hide" data-hide-tip="page:${PAGE}">Hide tips</button></div><ol>${p.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`;
    hero.after(box);
  }
  function tabTips() {
    for (const panel of document.querySelectorAll("section.panel[id^='tab-']")) {
      const k = panel.id.slice(4); const tip = TABS[k]; const key = `tab:${PAGE}:${k}`;
      const old = panel.querySelector(":scope > .xp-tip");
      if (!tip || tipsOff() || hidden()[key]) { if (old) old.remove(); continue; }
      if (old) continue;
      const d = document.createElement("div"); d.className = "xp-tip";
      d.innerHTML = `<span class="xp-tip-i" aria-hidden="true">i</span><div><b>What you're looking at:</b> ${esc(tip.w)} <b>What to do:</b> ${esc(tip.d)}</div><button type="button" class="xp-hide" data-hide-tip="${esc(key)}" aria-label="Hide this tip">Got it</button>`;
      panel.prepend(d);
    }
  }
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-hide-tip]"); if (!b || explainMode) return;
    hideTip(b.dataset.hideTip); const box = b.closest(".xp-tip, .xp-steps"); if (box) box.remove();
  });
  function setTips(on) { store.set("omega.xp.off", !on); if (on) store.set("omega.xp.hidden", {}); pageTip(); tabTips(); }

  // ------------------------------------------------------------------ help menu (the "?" in the bar)
  function helpMenu(btn) {
    const old = document.querySelector(".xp-help-menu"); if (old) { old.remove(); return; }
    const m = document.createElement("div"); m.className = "xp-help-menu"; m.setAttribute("role", "menu");
    m.innerHTML = `<button type="button" role="menuitem" data-h="explain"><b>Explain mode</b><span>Tap anything to see what it does</span></button>
      <button type="button" role="menuitem" data-h="glossary"><b>Glossary</b><span>Every term in plain English</span></button>
      <button type="button" role="menuitem" data-h="tips"><b>${tipsOff() ? "Show page tips" : "Hide page tips"}</b><span>The “what to do” notes on each page</span></button>
      <button type="button" role="menuitem" data-h="tour"><b>Take the tour</b><span>One minute: how ideas work</span></button>`;
    document.body.appendChild(m);
    const r = btn.getBoundingClientRect();
    m.style.top = `${r.bottom + 8 + scrollY}px`; m.style.left = `${Math.max(8, Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 8)) + scrollX}px`;
    const off = (ev) => { if (!m.contains(ev.target) && ev.target !== btn) { m.remove(); document.removeEventListener("click", off, true); } };
    setTimeout(() => document.addEventListener("click", off, true));
    m.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-h]"); if (!b) return;
      m.remove(); document.removeEventListener("click", off, true);
      const h = b.dataset.h;
      if (h === "explain") setExplain(true);
      else if (h === "glossary") glossary();
      else if (h === "tips") setTips(tipsOff());
      else if (h === "tour" && root.OmegaShell) root.OmegaShell.tour(true);
    });
    m.querySelector("button").focus();
  }

  // ------------------------------------------------------------------ start
  function start() {
    annotate(document); pageTip(); tabTips();
    let t = 0;
    new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => { annotate(document); tabTips(); }, 120); }).observe(document.body, { childList: true, subtree: true });
    // tooltips on the main controls, for mouse users
    for (const b of document.querySelectorAll("nav.tabs button")) { const tip = TABS[b.dataset.tab]; if (tip && !b.title) b.title = tip.w; }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  root.OmegaExplain = { lookup, show, glossary, setExplain, helpMenu, setTips, terms: G };
})(window);
