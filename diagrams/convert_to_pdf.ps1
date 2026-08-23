# webScraping 図 → PDF 一括変換スクリプト
# 使用法: PowerShell でこのスクリプトを実行
# 前提: Node.js がインストールされていること (npx が使えること)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $PSCommandPath

# 変換するファイル一覧
$diagrams = @(
    @{ Input = "01_flowchart.mmd";      Output = "01_flowchart.pdf";      Config = $null }
    @{ Input = "02_sequence.mmd";       Output = "02_sequence.pdf";       Config = $null }
    @{ Input = "03a_class_rules.mmd";   Output = "03a_class_rules.pdf";   Config = "puppeteer_landscape.json" }
    @{ Input = "03b_class_modules.mmd"; Output = "03b_class_modules.pdf"; Config = $null }
    @{ Input = "04_mindmap.mmd";        Output = "04_mindmap.pdf";        Config = $null }
    @{ Input = "05_state.mmd";          Output = "05_state.pdf";          Config = $null }
)

Write-Host "=== webScraping 図 → PDF 一括変換 ===" -ForegroundColor Cyan
Write-Host ""

foreach ($d in $diagrams) {
    $inputFile = Join-Path $ScriptDir $d.Input
    $outputFile = Join-Path $ScriptDir $d.Output

    if (-not (Test-Path $inputFile)) {
        Write-Warning "入力ファイルが見つかりません: $inputFile"
        continue
    }

    Write-Host "変換中: $($d.Input) → $($d.Output) ... " -NoNewline

    try {
        if ($d.Config) {
            $configFile = Join-Path $ScriptDir $d.Config
            npx.cmd -y @mermaid-js/mermaid-cli -i $inputFile -o $outputFile -p $configFile --pdfFit *>&1
        } else {
            npx.cmd -y @mermaid-js/mermaid-cli -i $inputFile -o $outputFile --pdfFit *>&1
        }
        Write-Host "完了" -ForegroundColor Green
    } catch {
        Write-Host "エラー: $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== 変換完了 ===" -ForegroundColor Cyan
Write-Host "出力先: $ScriptDir"
Write-Host ""
Write-Host "個別変換のコマンド例:" -ForegroundColor DarkGray
Write-Host "  npx.cmd -y @mermaid-js/mermaid-cli -i 01_flowchart.mmd -o 01_flowchart.pdf --pdfFit"
Write-Host "  npx.cmd -y @mermaid-js/mermaid-cli -i 03a_class_rules.mmd -o 03a_class_rules.pdf -p puppeteer_landscape.json --pdfFit"
Write-Host "  npx.cmd -y @mermaid-js/mermaid-cli -i 03b_class_modules.mmd -o 03b_class_modules.pdf --pdfFit"