// Готовит иконки приложения из присланного изображения (tools/icon_source.webp).
//
//   dart run tools/make_icon.dart
//   dart run flutter_launcher_icons
//
// Создаёт в assets/icon/: noctis_icon.png (1024, значок целиком), noctis_icon_foreground.png (для адаптивной иконки Android,
// рисунок в «безопасной зоне» на прозрачном фоне) и noctis_logo.png (256, логотип внутри приложения).
import 'dart:io';

import 'package:image/image.dart' as img;

void main() {
  final src = img.decodeImage(File('tools/icon_source.webp').readAsBytesSync());
  if (src == null) {
    stderr.writeln('Не удалось прочитать tools/icon_source.webp');
    exit(1);
  }
  // В исходнике значок лежит на белом фоне с запасом по краям: берём внутреннюю часть (чёрное поле значка без белых углов).
  final inset = (src.width * 0.104).round();
  final side = src.width - 2 * inset;
  final crop = img.copyCrop(src, x: inset, y: inset, width: side, height: side);

  final icon = img.copyResize(crop, width: 1024, height: 1024, interpolation: img.Interpolation.cubic);
  File('assets/icon/noctis_icon.png').writeAsBytesSync(img.encodePng(icon));

  // Адаптивная иконка Android: рисунок занимает ~66 % холста
  final fg = img.Image(width: 1024, height: 1024, numChannels: 4);
  final inner = img.copyResize(crop, width: 676, height: 676, interpolation: img.Interpolation.cubic);
  img.compositeImage(fg, inner, dstX: 174, dstY: 174);
  File('assets/icon/noctis_icon_foreground.png').writeAsBytesSync(img.encodePng(fg));

  final logo = img.copyResize(crop, width: 256, height: 256, interpolation: img.Interpolation.cubic);
  File('assets/icon/noctis_logo.png').writeAsBytesSync(img.encodePng(logo));
  stdout.writeln('готово: assets/icon/*.png');
}
