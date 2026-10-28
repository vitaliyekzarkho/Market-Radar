# OANDA time convention — evidence limits

Observed local evidence on 27 October: raw ticks differed from independent UTC by approximately +3600 seconds; the 14:00 M15/H1 rollover corroborated that window. The later 14:30 observation produced only a candidate because no rollover occurred. These are bounded measurements, not historical server rules.

[MetaQuotes Python rates documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py) specifies UTC epochs. The measured instance discrepancy therefore requires explicit source-specific handling; a naive Python timezone conversion is not independent broker proof.

[Official OANDA TMS MT5 guide](https://help.oanda.com/eu/en/faqs/mt5-user-guide-eu.htm) identifies the TMS service and ties daily candles to instrument sessions, but the reviewed page does not specify the server's historical GMT/DST conversion. That session statement does not prove raw epoch timezone offsets.

[OANDA Australia sub-account guide](https://www.oanda.com/au-en/trading/sub-account-differences/) describes MT5 chart alignment to New York close. It applies to that entity/context and is not adopted for TMS. The Japan historical CSV FAQ likewise cannot establish TMS semantics. No community broker listing is used as an authority.

Europe and US DST do not necessarily change on the same date. A successful +3600 current observation cannot choose a historic Europe/Warsaw, fixed CET or New York-aligned mapping. Missing mapping remains UNKNOWN. Session gaps and daily/weekly closes must be distinguished from appearance of the next bar; next-bar appearance gives a closed-by bound, not necessarily exchange close time.
