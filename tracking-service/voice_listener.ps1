param(
    [string]$OutputPath,
    [string]$Host = "127.0.0.1",
    [int]$Port = 5054
)

Add-Type -AssemblyName System.Speech

function Send-VoiceCommand {
    param(
        [string]$Text,
        [double]$Confidence
    )

    $payload = @{
        command = $Text
        confidence = [math]::Round($Confidence, 3)
        timestamp = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json -Compress

    $client = New-Object System.Net.Sockets.UdpClient
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
        [void]$client.Send($bytes, $bytes.Length, $Host, $Port)
    } finally {
        $client.Dispose()
    }

    if ($OutputPath) {
        Set-Content -LiteralPath $OutputPath -Value $payload -Encoding UTF8
    }
}

$commands = @(
    "jugar",
    "calibrar",
    "creditos",
    "salir",
    "siguiente",
    "atras",
    "volver",
    "tutorial",
    "repetir",
    "regresar",
    "menu",
    "pausa",
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

    Send-VoiceCommand -Text $text -Confidence $confidence
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
