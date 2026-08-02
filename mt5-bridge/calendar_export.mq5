//+------------------------------------------------------------------+
//|  calendar_export.mq5                                             |
//|                                                                  |
//|  Exports the terminal's economic calendar to JSON for the         |
//|  read-only MT5 bridge.                                           |
//|                                                                  |
//|  The MetaTrader5 Python package exposes no calendar API —         |
//|  CalendarValueHistory() and friends are MQL5-only — so this       |
//|  script writes MQL5/Files/mcp_calendar.json and the Python side   |
//|  reads it back. That file is what powers the news blackout check. |
//|                                                                  |
//|  Install: copy to <terminal data>/MQL5/Scripts/, compile in       |
//|  MetaEditor (F7), then run it on any chart. Re-run to refresh —   |
//|  daily is plenty, since scheduled events rarely move.             |
//|                                                                  |
//|  Values are written as raw MQL5 longs (scaled by 1e6, with        |
//|  "no value" as null); normalize.py decodes them.                  |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input int    DaysBack   = 7;                    // history window (impact studies)
input int    DaysAhead  = 21;                   // forward window (blackout checks)
input string OutFile    = "mcp_calendar.json";  // written to MQL5/Files/
input string Currencies = "";                   // e.g. "USD,EUR" — empty exports all

//+------------------------------------------------------------------+
string JsonEscape(const string text)
  {
   string out = "";
   int len = StringLen(text);
   for(int i = 0; i < len; i++)
     {
      ushort ch = StringGetCharacter(text, i);
      switch(ch)
        {
         case '"':  out += "\\\""; break;
         case '\\': out += "\\\\"; break;
         case '\n': out += "\\n";  break;
         case '\r': out += "\\r";  break;
         case '\t': out += "\\t";  break;
         default:
            // Control characters would make the JSON unparseable; drop them.
            if(ch >= 32)
               out += ShortToString(ch);
        }
     }
   return out;
  }

//+------------------------------------------------------------------+
// LONG_MIN is MQL5's "this field is empty" marker for calendar values.
string LongOrNull(const long value)
  {
   if(value == LONG_MIN)
      return "null";
   return IntegerToString(value);
  }

//+------------------------------------------------------------------+
bool CurrencyWanted(const string currency, const string &wanted[])
  {
   int count = ArraySize(wanted);
   if(count == 0)
      return true;
   for(int i = 0; i < count; i++)
      if(wanted[i] == currency)
         return true;
   return false;
  }

//+------------------------------------------------------------------+
void OnStart()
  {
   string wanted[];
   if(StringLen(Currencies) > 0)
     {
      StringSplit(Currencies, ',', wanted);
      for(int i = 0; i < ArraySize(wanted); i++)
        {
         StringTrimLeft(wanted[i]);
         StringTrimRight(wanted[i]);
         StringToUpper(wanted[i]);
        }
     }

   // MQL5 reports calendar event times against the SERVER clock, not UTC.
   // The terminal knows its own offset, so resolve it here and write real UTC
   // into the file — that way the bridge never has to guess, and the calendar
   // keeps working on a weekend when no ticks are arriving to infer it from.
   int offsetSec = (int)(TimeCurrent() - TimeGMT());
   // Snap to the nearest half hour: TimeCurrent() and TimeGMT() are read a
   // moment apart and real offsets are whole or half hours.
   offsetSec = (int)(MathRound(offsetSec / 1800.0) * 1800);

   datetime from = TimeCurrent() - (datetime)DaysBack  * 86400;
   datetime to   = TimeCurrent() + (datetime)DaysAhead * 86400;

   MqlCalendarValue values[];
   int total = CalendarValueHistory(values, from, to);
   if(total <= 0)
     {
      Print("calendar_export: CalendarValueHistory returned ", total,
            " (error ", GetLastError(), "). ",
            "Check that the calendar is enabled and your broker populates it.");
      return;
     }

   int handle = FileOpen(OutFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      Print("calendar_export: cannot open ", OutFile, " (error ", GetLastError(), ")");
      return;
     }

   FileWriteString(handle, "{\n");
   FileWriteString(handle, "  \"exported_at\": \"" +
                   TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS) + "\",\n");
   FileWriteString(handle, "  \"format\": 2,\n");
   FileWriteString(handle, "  \"offset_sec\": " + IntegerToString(offsetSec) + ",\n");
   FileWriteString(handle, "  \"events\": [\n");

   int written = 0;
   for(int i = 0; i < total; i++)
     {
      MqlCalendarEvent event;
      if(!CalendarEventById(values[i].event_id, event))
         continue;

      MqlCalendarCountry country;
      string currency = "";
      string countryName = "";
      if(CalendarCountryById(event.country_id, country))
        {
         currency    = country.currency;
         countryName = country.name;
        }

      if(!CurrencyWanted(currency, wanted))
         continue;

      if(written > 0)
         FileWriteString(handle, ",\n");

      string row = StringFormat(
         "    {\"time\": %d, \"time_utc\": %d, \"currency\": \"%s\", \"country\": \"%s\", "
         "\"event\": \"%s\", \"importance\": %d, \"digits\": %d, \"unit\": \"%s\", "
         "\"actual\": %s, \"forecast\": %s, \"previous\": %s}",
         (long)values[i].time,
         (long)values[i].time - offsetSec,
         JsonEscape(currency),
         JsonEscape(countryName),
         JsonEscape(event.name),
         (int)event.importance,
         (int)event.digits,
         JsonEscape(EnumToString(event.unit)),
         LongOrNull(values[i].actual_value),
         LongOrNull(values[i].forecast_value),
         LongOrNull(values[i].prev_value));

      FileWriteString(handle, row);
      written++;
     }

   FileWriteString(handle, "\n  ]\n}\n");
   FileClose(handle);

   Print("calendar_export: wrote ", written, " events to MQL5/Files/", OutFile,
         " (window ", TimeToString(from, TIME_DATE), " .. ",
         TimeToString(to, TIME_DATE),
         ", server offset ", offsetSec, "s from UTC)");
   if(DaysBack > 200)
      Print("calendar_export: note — the offset written is the CURRENT one. ",
            "Events on the far side of a daylight-saving change may be off by ",
            "an hour. Re-export regularly rather than relying on one long pull.");
  }
//+------------------------------------------------------------------+
