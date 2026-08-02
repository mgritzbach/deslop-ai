param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$OutJson
)

$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $Path).Path
$extension = [System.IO.Path]::GetExtension($resolved).ToLowerInvariant()
$result = [ordered]@{
    applicable = $true
    passed = $false
    application = $null
    reason = $null
    repaired = $false
    overflow = @()
    objectOverlaps = @()
    pageCount = $null
}

function Release-ComObject([object]$Object) {
    if ($null -ne $Object -and [System.Runtime.InteropServices.Marshal]::IsComObject($Object)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Object)
    }
}

try {
    if ($extension -eq '.pptx') {
        $result.application = 'PowerPoint'
        $app = New-Object -ComObject PowerPoint.Application
        $app.DisplayAlerts = 1
        $presentation = $app.Presentations.Open($resolved, $true, $false, $false)
        $result.slideCount = $presentation.Slides.Count
        for ($slideIndex = 1; $slideIndex -le $presentation.Slides.Count; $slideIndex++) {
            $slide = $presentation.Slides.Item($slideIndex)
            $descriptors = @()
            for ($shapeIndex = 1; $shapeIndex -le $slide.Shapes.Count; $shapeIndex++) {
                $shape = $slide.Shapes.Item($shapeIndex)
                try {
                    $isText = [bool]($shape.HasTextFrame -and $shape.TextFrame2.HasText)
                    $isChart = $false
                    try { $isChart = [bool]$shape.HasChart } catch { }
                    $left = [double]$shape.Left; $top = [double]$shape.Top; $right = [double]($shape.Left + $shape.Width); $bottom = [double]($shape.Top + $shape.Height)
                    if ($isText) {
                        try {
                            $left = [double]$shape.TextFrame2.TextRange.BoundLeft
                            $top = [double]$shape.TextFrame2.TextRange.BoundTop
                            $right = $left + [double]$shape.TextFrame2.TextRange.BoundWidth
                            $bottom = $top + [double]$shape.TextFrame2.TextRange.BoundHeight
                        } catch { }
                    }
                    $descriptors += [pscustomobject]@{ Index = $shapeIndex; Left = $left; Top = $top; Right = $right; Bottom = $bottom; IsText = $isText; IsChart = $isChart }
                    if ($shape.HasTextFrame -and $shape.TextFrame2.HasText) {
                        # PowerPoint wraps ordinary text horizontally, so BoundWidth can
                        # exceed shape width without visual overflow. Height is the safe gate.
                        $heightOverflow = $shape.TextFrame2.TextRange.BoundHeight -gt ($shape.Height + 1)
                        if ($heightOverflow) {
                            $result.overflow += "slide:${slideIndex}:shape:${shapeIndex}"
                        }
                    }
                } catch { }
                Release-ComObject $shape
            }
            for ($a = 0; $a -lt $descriptors.Count; $a++) {
                for ($b = $a + 1; $b -lt $descriptors.Count; $b++) {
                    $first = $descriptors[$a]; $second = $descriptors[$b]
                    if (($first.IsText -and $second.IsChart) -or ($first.IsChart -and $second.IsText)) {
                        $intersects = $first.Left -lt $second.Right -and $first.Right -gt $second.Left -and $first.Top -lt $second.Bottom -and $first.Bottom -gt $second.Top
                        if ($intersects) { $result.objectOverlaps += "slide:${slideIndex}:shape:$($first.Index):shape:$($second.Index)" }
                    }
                }
            }
            Release-ComObject $slide
        }
        $presentation.Close()
        $app.Quit()
        $result.passed = $result.overflow.Count -eq 0
        $result.reason = if ($result.passed) { "PowerPoint opened the file without repair and reported no text overflow. Text/chart geometry overlaps recorded: $($result.objectOverlaps.Count)." } else { 'PowerPoint opened the file, but potential text overflow was detected.' }
        Release-ComObject $presentation
        Release-ComObject $app
    } elseif ($extension -eq '.docx') {
        $result.application = 'Word'
        $app = New-Object -ComObject Word.Application
        $app.Visible = $false
        $app.DisplayAlerts = 0
        $document = $app.Documents.Open($resolved, $false, $true, $false)
        $result.pageCount = $document.ComputeStatistics(2)
        $document.Close(0)
        $app.Quit()
        $result.passed = $true
        $result.reason = 'Word opened the file read-only without repair.'
        Release-ComObject $document
        Release-ComObject $app
    } else {
        $result.applicable = $false
        $result.passed = $true
        $result.reason = 'Office verification is not required for this format.'
    }
} catch {
    $result.passed = $false
    $result.reason = $_.Exception.Message
    try { if ($null -ne $document) { $document.Close(0) } } catch { }
    try { if ($null -ne $presentation) { $presentation.Close() } } catch { }
    try { if ($null -ne $app) { $app.Quit() } } catch { }
} finally {
    Release-ComObject $document
    Release-ComObject $presentation
    Release-ComObject $app
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$parent = Split-Path -Parent $OutJson
if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    [void](New-Item -ItemType Directory -Path $parent)
}
$json = $result | ConvertTo-Json -Depth 8
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($OutJson, $json, $utf8)
if (-not $result.passed) { exit 2 }
