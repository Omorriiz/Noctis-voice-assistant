/// Часовые пояса (названия IANA), из которых можно выбрать. Текущее значение колонки добавляется, если его нет в списке.
const List<String> kTimezones = [
  'Europe/Kaliningrad', 'Europe/Moscow', 'Europe/Samara', 'Asia/Yekaterinburg', 'Asia/Omsk', 'Asia/Novosibirsk', 'Asia/Krasnoyarsk', 'Asia/Irkutsk',
  'Asia/Yakutsk', 'Asia/Vladivostok', 'Asia/Magadan', 'Asia/Kamchatka', 'Europe/Kyiv', 'Europe/Minsk', 'Europe/Chisinau', 'Asia/Tbilisi', 'Asia/Yerevan',
  'Asia/Baku', 'Asia/Almaty', 'Asia/Tashkent', 'UTC', 'Europe/London', 'Europe/Dublin', 'Europe/Lisbon', 'Europe/Madrid', 'Europe/Paris', 'Europe/Berlin',
  'Europe/Rome', 'Europe/Amsterdam', 'Europe/Brussels', 'Europe/Zurich', 'Europe/Vienna', 'Europe/Prague', 'Europe/Warsaw', 'Europe/Stockholm',
  'Europe/Helsinki', 'Europe/Athens', 'Europe/Istanbul', 'Asia/Jerusalem', 'Asia/Dubai', 'Asia/Karachi', 'Asia/Kolkata', 'Asia/Dhaka', 'Asia/Bangkok',
  'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Singapore', 'Asia/Seoul', 'Asia/Tokyo', 'Australia/Perth', 'Australia/Sydney', 'Pacific/Auckland',
  'Atlantic/Reykjavik', 'America/Sao_Paulo', 'America/Argentina/Buenos_Aires', 'America/Santiago', 'America/Bogota', 'America/Mexico_City',
  'America/New_York', 'America/Toronto', 'America/Chicago', 'America/Denver', 'America/Phoenix', 'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu',
  'Africa/Cairo', 'Africa/Lagos', 'Africa/Johannesburg', 'Africa/Nairobi',
];

List<String> timezoneChoices(String current) => kTimezones.contains(current) ? kTimezones : [current, ...kTimezones];
