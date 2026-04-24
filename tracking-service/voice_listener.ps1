param(
    [string]$OutputPath
)

Add-Type -AssemblyName System.Speech

$commands = @(
    "jugar",
    "calibrar",
    "creditos",
    "salir",
    "siguiente",
    "atras",
    "volver",
    "menu",
    "continuar",
    "reiniciar",
    "cambiar nivel",
    "facil",
    "medio",
    "normal",
    "dificil",
    "si",
    "no",
    "menu"
)

$recognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
$selectedRecognizer = $recognizers | Where-Object { $_.Culture.Name -like "es-*" } | Select-Object -First 1
if ($null -eq $selectedRecognizer) {
    $selectedRecognizer = $recognizers | Select-Object -First 1
}
if ($null -eq $selectedRecognizer) {
    throw "No hay reconocedores de voz instalados en Windows."
}

$culture = $selectedRecognizer.Culture
$choices = New-Object System.Speech.Recognition.Choices
$choices.Add($commands)
$grammarBuilder = New-Object System.Speech.Recognition.GrammarBuilder
$grammarBuilder.Culture = $culture
$grammarBuilder.Append($choices)
$grammar = New-Object System.Speech.Recognition.Grammar($grammarBuilder)

$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$engine.LoadGrammar($grammar)
$engine.SetInputToDefaultAudioDevice()

Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized -Action {
    $text = $Event.SourceEventArgs.Result.Text
    $confidence = $Event.SourceEventArgs.Result.Confidence
    if ($confidence -lt 0.55) {
        return
    }

    $payload = @{
        command = $text
        confidence = [math]::Round($confidence, 3)
        timestamp = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json -Compress

    Set-Content -LiteralPath $OutputPath -Value $payload -Encoding UTF8
} | Out-Null

$engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)

try {
    while ($true) {
        Start-Sleep -Milliseconds 250
    }
} finally {
    $engine.RecognizeAsyncStop()
    $engine.Dispose()
}
