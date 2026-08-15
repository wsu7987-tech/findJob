param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [ValidateSet("stub", "real-provider")]
    [string]$Mode = "stub",
    [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"

function Invoke-ApiJson {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("GET", "POST", "PATCH", "DELETE")]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [object]$Body
    )

    $request = @{
        Method  = $Method
        Uri     = $Uri
        Headers = @{ Accept = "application/json" }
    }

    if ($null -ne $Body) {
        $request.ContentType = "application/json"
        $request.Body = ($Body | ConvertTo-Json -Depth 8)
    }

    return Invoke-RestMethod @request
}

function Assert-Condition {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

$trimmedBaseUrl = $BaseUrl.TrimEnd("/")
$apiBase = "$trimmedBaseUrl/api"
$suffix = [guid]::NewGuid().ToString("N")
$createdItemIds = [System.Collections.Generic.List[string]]::new()
$createdItemId = $null
$summaryRunId = $null
$reportRunId = $null
$resultSnapshotId = $null
$deliveryChainItemIds = [System.Collections.Generic.List[string]]::new()
$deliveryChainTitles = [System.Collections.Generic.Dictionary[string, string]]::new()
$knownLimitations = [System.Collections.Generic.List[string]]::new()
$blockedChecks = [System.Collections.Generic.List[string]]::new()
$validatedScope = [System.Collections.Generic.List[string]]::new()

function New-PoolItem {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceType,
        [Parameter(Mandatory = $true)]
        [string]$SourceValue,
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [string]$RawText
    )

    $body = @{
        source_type = $SourceType
        source_value = $SourceValue
        title = $Title
    }
    if (-not [string]::IsNullOrWhiteSpace($RawText)) {
        $body.raw_text = $RawText
    }

    $created = Invoke-ApiJson -Method POST -Uri "$apiBase/pool/items" -Body $body
    $createdId = $created.item.id
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($createdId)) "Create pool item did not return an item id."
    $createdItemIds.Add($createdId)
    return $created
}

function Get-ModeScope {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("stub", "real-provider")]
        [string]$Mode
    )

    if ($Mode -eq "stub") {
        return [pscustomobject]@{
            mode = $Mode
            proves = @(
                'stub mode proves normalized raw_text can complete summary -> report -> results',
                'pool, run, and feedback APIs still work'
            )
            does_not_prove = @(
                'URL/PDF/Markdown were truly parsed from files or webpages',
                'real LLM / embedding / RAG are connected',
                'Electron file picking and shell interactions are verified'
            )
        }
    }

    return [pscustomobject]@{
        mode = $Mode
        proves = @(
            'real files or URLs, real providers, and the Electron shell all participated',
            'parsing, summarization, vector writes, and result navigation worked end to end'
        )
        does_not_prove = @(
            'future parallel changes do not need re-verification',
            'browser-only integration can replace desktop shell acceptance'
        )
    }
}

Write-Host "Smoke testing backend at $trimmedBaseUrl"

try {
    $health = Invoke-ApiJson -Method GET -Uri "$apiBase/health"
    Assert-Condition ($health.status -eq "ok") "Health check did not return status=ok."
    $validatedScope.Add("health")

    $config = Invoke-ApiJson -Method GET -Uri "$apiBase/config"
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.output_root)) "Config read did not return output_root."
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.summary_output_dir)) "Config read did not return summary_output_dir."
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.report_output_dir)) "Config read did not return report_output_dir."
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.llm_provider)) "Config is missing llm_provider."
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.llm_model)) "Config is missing llm_model."
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.embedding_provider)) "Config is missing embedding_provider."
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.embedding_model)) "Config is missing embedding_model."
    $validatedScope.Add("config")

    $sourceMatrix = @(
        @{ source_type = "url"; source_value = "https://example.com/$suffix"; title = "Smoke URL $suffix"; raw_text = "Fetched URL text for smoke $suffix." },
        @{ source_type = "pdf"; source_value = "D:/fixtures/smoke-$suffix.pdf"; title = "Smoke PDF $suffix"; raw_text = "Extracted PDF text for smoke $suffix." },
        @{ source_type = "markdown"; source_value = "D:/fixtures/smoke-$suffix.md"; title = "Smoke Markdown $suffix"; raw_text = "# Smoke Markdown $suffix" },
        @{ source_type = "text"; source_value = "manual-smoke-$suffix"; title = "Manual smoke $suffix"; raw_text = "Manual smoke test payload created at $(Get-Date -Format o)." }
    )
    $createdItems = foreach ($scenario in $sourceMatrix) {
        New-PoolItem -SourceType $scenario.source_type -SourceValue $scenario.source_value -Title $scenario.title -RawText $scenario.raw_text
    }
    $created = $createdItems | Where-Object { $_.item.source_type -eq "text" } | Select-Object -First 1
    $createdItemId = $created.item.id
    foreach ($scenario in ($sourceMatrix | Where-Object { $_.source_type -in @("pdf", "markdown", "text") })) {
        $item = $createdItems | Where-Object { $_.item.source_type -eq $scenario.source_type } | Select-Object -First 1
        Assert-Condition ($null -ne $item) "Delivery chain source_type=$($scenario.source_type) was not created."
        $deliveryChainItemIds.Add($item.item.id)
        $deliveryChainTitles[$item.item.title] = $scenario.raw_text
    }

    $pool = Invoke-ApiJson -Method GET -Uri "$apiBase/pool/items"
    Assert-Condition ($pool.total -ge $sourceMatrix.Count) "Pool listing did not include the source matrix items."
    foreach ($scenario in $sourceMatrix) {
        $matched = $pool.items | Where-Object {
            $_.source_type -eq $scenario.source_type -and $_.source_value -eq $scenario.source_value
        } | Select-Object -First 1
        Assert-Condition ($null -ne $matched) "Pool listing did not contain source_type=$($scenario.source_type)."
    }
    $validatedScope.Add("pool-ingestion-source-matrix")

    $precheck = Invoke-ApiJson -Method GET -Uri "$apiBase/summary/precheck"
    Assert-Condition ($precheck.count -ge $deliveryChainItemIds.Count) "Summary precheck returned fewer items than expected for the delivery chain."
    Assert-Condition (($precheck.items.id -contains $createdItemId)) "Created pool item was not present in summary precheck."
    foreach ($deliveryItemId in $deliveryChainItemIds) {
        Assert-Condition (($precheck.items.id -contains $deliveryItemId)) "Summary precheck did not include delivery-chain pool item $deliveryItemId."
    }
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($precheck.output_dir)) "Summary precheck did not return output_dir."

    $runCreate = Invoke-ApiJson -Method POST -Uri "$apiBase/summary/runs" -Body @{ pool_ids = $deliveryChainItemIds.ToArray() }
    $summaryRunId = $runCreate.run_id
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($summaryRunId)) "Summary run creation did not return a run id."
    Assert-Condition ($runCreate.status -eq "completed") "Summary run creation did not complete successfully."
    Assert-Condition ($runCreate.stage -eq "completed") "Summary run creation did not finish in the completed stage."
    $validatedScope.Add("summary-run-pdf-markdown-text")

    $run = Invoke-ApiJson -Method GET -Uri "$apiBase/runs/$summaryRunId"
    Assert-Condition ($run.run_id -eq $summaryRunId) "Fetched run id did not match the created run id."
    Assert-Condition ($run.status -eq "completed") "Fetched run status was not completed."
    Assert-Condition ($run.succeeded_items -ge $deliveryChainItemIds.Count) "Summary run did not complete the expected delivery-chain items."

    $summaryRuns = Invoke-ApiJson -Method GET -Uri "$apiBase/runs?task_type=summary"
    Assert-Condition ($summaryRuns.total -ge 1) "Summary runs list returned no items."
    Assert-Condition (($summaryRuns.items.run_id -contains $summaryRunId)) "Created summary run was not present in /api/runs."
    $validatedScope.Add("runs-list")

    $eventsResponse = Invoke-WebRequest -Method GET -Uri "$apiBase/runs/$summaryRunId/events"
    Assert-Condition ($eventsResponse.Content -match "event:\s+run\.") "Run events response did not contain an SSE event line."
    Assert-Condition ($eventsResponse.Content -match [regex]::Escape($summaryRunId)) "Run events response did not contain the run id."
    $validatedScope.Add("sse-events")

    $resummarize = Invoke-ApiJson -Method POST -Uri "$apiBase/pool/items/$createdItemId/resummarize"
    Assert-Condition ($resummarize.accepted -eq $true) "Resummarize endpoint did not accept the request."

    $poolAfterResummarize = Invoke-ApiJson -Method GET -Uri "$apiBase/pool/items"
    $resummarizedItem = $poolAfterResummarize.items | Where-Object { $_.id -eq $createdItemId } | Select-Object -First 1
    Assert-Condition ($null -ne $resummarizedItem) "Resummarized pool item is missing from the pool list."
    Assert-Condition ($resummarizedItem.current_status -eq "pending") "Resummarized pool item did not reset to pending."
    Assert-Condition ($resummarizedItem.was_resummarized -eq $true) "Resummarized pool item did not mark was_resummarized=true."

    $rerunCreate = Invoke-ApiJson -Method POST -Uri "$apiBase/summary/runs" -Body @{ pool_ids = @($createdItemId) }
    Assert-Condition ($rerunCreate.status -eq "completed") "Resummarized item did not complete successfully."
    $validatedScope.Add("resummarize")

    $reportPrecheck = Invoke-ApiJson -Method GET -Uri "$apiBase/report/precheck"
    $weekKey = $reportPrecheck.week_key
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($weekKey)) "Report precheck did not return week_key."
    Assert-Condition ($reportPrecheck.next_version -ge 1) "Report precheck returned an invalid next_version."

    $reportCreate = Invoke-ApiJson -Method POST -Uri "$apiBase/report/runs" -Body @{ week_key = $weekKey }
    $reportRunId = $reportCreate.run_id
    $reportVersion = [int]$reportCreate.version
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($reportRunId)) "Report run creation did not return a run id."
    Assert-Condition ($reportCreate.week_key -eq $weekKey) "Report run creation returned an unexpected week_key."
    Assert-Condition ($reportVersion -ge 1) "Report run creation returned an invalid version."

    $reportRuns = Invoke-ApiJson -Method GET -Uri "$apiBase/runs?task_type=report"
    Assert-Condition ($reportRuns.total -ge 1) "Report runs list returned no items."
    Assert-Condition (($reportRuns.items.run_id -contains $reportRunId)) "Created report run was not present in /api/runs."

    $reportVersions = Invoke-ApiJson -Method GET -Uri "$apiBase/reports/$weekKey/versions"
    Assert-Condition ($reportVersions.items.Count -ge 1) "Report versions list returned no items."
    Assert-Condition (($reportVersions.items.version -contains $reportVersion)) "Created report version was not present in report versions list."

    $reportDetail = Invoke-ApiJson -Method GET -Uri "$apiBase/reports/$weekKey/versions/$reportVersion"
    Assert-Condition ($reportDetail.week_key -eq $weekKey) "Report detail returned an unexpected week_key."
    Assert-Condition ($reportDetail.version -eq $reportVersion) "Report detail returned an unexpected version."
    Assert-Condition ($reportDetail.markdown_content -match "Weekly Report") "Report detail markdown did not contain the expected heading."
    Assert-Condition ($reportDetail.snapshot_payload.items.Count -ge $deliveryChainItemIds.Count) "Report detail did not expose enough snapshot_payload.items."
    Assert-Condition ($reportDetail.snapshot_payload.source_distribution.pdf -ge 1) "Report detail did not include a summarized PDF item."
    Assert-Condition ($reportDetail.snapshot_payload.source_distribution.markdown -ge 1) "Report detail did not include a summarized Markdown item."
    Assert-Condition ($reportDetail.snapshot_payload.source_distribution.text -ge 1) "Report detail did not include a summarized text item."

    $reportItems = @{}
    foreach ($reportItem in $reportDetail.snapshot_payload.items) {
        $reportItems[$reportItem.title] = $reportItem.snapshot_id
    }
    foreach ($expectedTitle in $deliveryChainTitles.Keys) {
        Assert-Condition ($reportItems.ContainsKey($expectedTitle)) "Report detail did not include summarized item '$expectedTitle'."
    }
    $resultSnapshotId = $reportItems[$created.item.title]
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($resultSnapshotId)) "Report detail item did not expose snapshot_id."
    $validatedScope.Add("report")

    foreach ($deliveryTitle in $deliveryChainTitles.Keys) {
        $deliverySnapshotId = $reportItems[$deliveryTitle]
        $deliveryResultDetail = Invoke-ApiJson -Method GET -Uri "$apiBase/results/$deliverySnapshotId"
        Assert-Condition ($deliveryResultDetail.id -eq $deliverySnapshotId) "Result detail did not return the requested snapshot."
        Assert-Condition ($deliveryResultDetail.summary_text -eq $deliveryChainTitles[$deliveryTitle]) "Result detail summary_text did not match the normalized content for '$deliveryTitle'."
    }

    $resultPatch = Invoke-ApiJson -Method PATCH -Uri "$apiBase/results/$resultSnapshotId" -Body @{
        final_category = "manual-smoke"
        final_tags = @("manual-smoke", $suffix)
    }
    Assert-Condition ($resultPatch.final_category -eq "manual-smoke") "Result patch did not update final_category."
    Assert-Condition (($resultPatch.final_tags -join ",") -eq "manual-smoke,$suffix") "Result patch did not update final_tags."

    $feedback = Invoke-ApiJson -Method POST -Uri "$apiBase/results/$resultSnapshotId/feedback" -Body @{ feedback_value = "useful" }
    Assert-Condition ($feedback.saved -eq $true) "Result feedback did not report saved=true."
    $validatedScope.Add("result-detail-feedback")

    $knownLimitations.Add("Smoke proves PDF/Markdown/Text can complete the stub delivery chain when normalized text is already provided; it does not prove those files were truly parsed from disk.")
    $knownLimitations.Add("URL source is only validated for API acceptance in this smoke; it is not treated here as proof of real webpage fetching or parsing.")
    $knownLimitations.Add("Embedding/RAG retrieval is still not verified here; qdrant_point_id existence alone is not treated as proof.")
    if ($Mode -eq "stub") {
        $knownLimitations.Add("Stub mode only proves the current minimal loop is runnable; it is not a complete real-provider acceptance result.")
    }
    else {
        $blockedChecks.Add("Dependency install/networking for parser, LLM, embedding, and Qdrant libraries must be ready before real-provider validation counts as complete.")
        $blockedChecks.Add("Real API key/base_url inputs must be prepared for the selected providers before real-provider validation counts as complete.")
        $blockedChecks.Add("Electron environment validation is still required for PDF file picking and desktop-shell behaviors.")
    }

    $result = [pscustomobject]@{
        mode = $Mode
        mode_scope = (Get-ModeScope -Mode $Mode)
        health_status = $health.status
        output_root = $config.output_root
        source_types_accepted = $sourceMatrix.source_type
        validated_scope = $validatedScope
        pool_item_id = $createdItemId
        summary_run_id = $summaryRunId
        run_status = $run.status
        report_run_id = $reportRunId
        report_week_key = $weekKey
        report_version = $reportVersion
        result_snapshot_id = $resultSnapshotId
        events_preview = ($eventsResponse.Content.Trim() -split "`r?`n" | Select-Object -First 2) -join " | "
        known_limitations = $knownLimitations
        blocked_checks = $blockedChecks
    }

    Write-Host "Manual smoke test passed."
    $result | ConvertTo-Json -Depth 4
}
finally {
    if (-not $SkipCleanup) {
        foreach ($cleanupItemId in $createdItemIds) {
            try {
                $cleanup = Invoke-ApiJson -Method DELETE -Uri "$apiBase/pool/items/$cleanupItemId"
                if ($cleanup.deleted) {
                    Write-Host "Cleaned up pool item $cleanupItemId"
                }
            }
            catch {
                Write-Warning "Cleanup failed for pool item ${cleanupItemId}: $($_.Exception.Message)"
            }
        }
    }
}
