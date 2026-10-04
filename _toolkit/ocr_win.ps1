# OCR через Windows.Media.Ocr с координатами слов.
#
# Зачем: второй, независимый способ прочтения изображения. Зрение (vision_analyze) даёт смысл и
# разметку схемы, но ошибается на мелком тексте и выдумывает бренды; Windows OCR даёт сырые строки
# и координаты каждого слова. Совпадение двух способов — основание считать число прочитанным верно,
# расхождение — повод померить пиксели (см. _toolkit/images-playbook.md).
#
# Координаты (X, Y, W, H) печатаются не для красоты: по X/Y видно, что зрение не переставило блоки
# местами, а по W можно измерить длину полосы в столбчатой диаграмме, когда цифры нечитаемы.
#
# Запуск:
#   powershell.exe -NoProfile -File _toolkit\ocr_win.ps1 -Path "<путь к картинке>\image.png" -Out "$env:TEMP\ocr.txt"
#   powershell.exe -NoProfile -File _toolkit\ocr_win.ps1 -Path img.png -Out out.txt -Lang en-US
#
# Формат вывода: первая строка «ENGINE: <язык>», далее TSV: X<TAB>Y<TAB>W<TAB>H<TAB>текст слова.
# Увеличение перед распознаванием (мелкий текст) делается заранее, средствами ffmpeg:
#   ffmpeg -i in.png -vf scale=iw*4:ih*4 out.png
param(
  [Parameter(Mandatory=$true)][string]$Path,
  [Parameter(Mandatory=$true)][string]$Out,
  [string]$Lang = "ru-RU"
)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}

[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null

if (-not (Test-Path $Path)) { Write-Error "no such file: $Path"; exit 1 }

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Path).Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

# Язык: сначала запрошенный, затем язык профиля, затем английский — чтобы не падать без нужного пакета.
$engine = $null
foreach ($tag in @($Lang, "ru-RU", "ru")) {
  try {
    $language = New-Object Windows.Globalization.Language($tag)
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
  } catch { }
  if ($null -ne $engine) { break }
}
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language("en-US"))) }
if ($null -eq $engine) { Write-Error "no OCR language available on this machine"; exit 2 }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("ENGINE: " + $engine.RecognizerLanguage.LanguageTag)
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $result.Lines) {
  foreach ($w in $line.Words) {
    $lines.Add(("{0}`t{1}`t{2}`t{3}`t{4}" -f [math]::Round($w.BoundingRect.X), [math]::Round($w.BoundingRect.Y),
      [math]::Round($w.BoundingRect.Width), [math]::Round($w.BoundingRect.Height), $w.Text))
  }
}
[System.IO.File]::WriteAllLines($Out, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("done: " + ($lines.Count - 1) + " words -> " + $Out + " (lang " + $engine.RecognizerLanguage.LanguageTag + ")")
