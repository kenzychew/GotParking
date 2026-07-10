import { useState } from "react";
import type { UseForecastResult } from "../hooks/useForecast";
import { copyToClipboard } from "../lib/clipboard";
import type { EffectiveTheme } from "../lib/colorTokens";
import { buildShareUrl } from "../lib/shareLink";
import { isStalePayload, minutesSince } from "../lib/staleness";
import { getSeedCarparkById } from "../seed/seedCarparks";
import { TierBadge } from "./TierBadge";

interface ForecastCardProps {
  carparkId: string;
  forecastQuery: UseForecastResult;
  theme: EffectiveTheme;
}

/**
 * The prediction card (design doc Design Details interaction table). Renders
 * one of: loading skeleton, server-error, offline (with last-seen data if
 * any), or success -- and within success, one of cold_start / stale /
 * fresh-forecast. Every branch shows the live count except the bare
 * server-error state (no live_lots is trustworthy when the server itself
 * is degraded).
 */
export function ForecastCard({ carparkId, forecastQuery, theme }: ForecastCardProps) {
  const displayName = getSeedCarparkById(carparkId)?.displayName ?? carparkId;

  if (forecastQuery.status === "loading") {
    return <ForecastCardSkeleton name={displayName} />;
  }

  if (forecastQuery.status === "server-error") {
    return (
      <div className="forecast-card" role="alert">
        <h2 className="forecast-card__name">{displayName}</h2>
        <p>Predictions temporarily unavailable</p>
        <div className="forecast-card__actions">
          <button type="button" className="button" onClick={forecastQuery.retry}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (forecastQuery.status === "offline") {
    const carpark = forecastQuery.data?.carparks.find((c) => c.carpark_id === carparkId);
    return (
      <div className="forecast-card" role="alert">
        <h2 className="forecast-card__name">{displayName}</h2>
        <p>No internet connection - showing last-seen data</p>
        {carpark ? <LiveCountLine liveLots={carpark.live_lots} /> : null}
        <div className="forecast-card__actions">
          <button type="button" className="button" onClick={forecastQuery.retry}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  // status === "success"
  const payload = forecastQuery.data;
  const carpark = payload?.carparks.find((c) => c.carpark_id === carparkId);
  if (!payload || !carpark) {
    return (
      <div className="forecast-card">
        <h2 className="forecast-card__name">{displayName}</h2>
        <p>No forecast data available for this carpark right now.</p>
      </div>
    );
  }

  const shareUrl = buildShareUrl(carparkId);

  return (
    <div className="forecast-card">
      <div className="forecast-card__header">
        <h2 className="forecast-card__name">{displayName}</h2>
        <ShareButton url={shareUrl} />
      </div>

      {carpark.state === "cold_start" ? (
        <>
          <p>Collecting data - check back in a few days</p>
          <LiveCountLine liveLots={carpark.live_lots} />
        </>
      ) : isStalePayload(payload.generated_at) ? (
        <>
          <p className="forecast-card__caveat">
            Data delayed - updated {minutesSince(payload.generated_at)}m ago
          </p>
          <LiveCountLine liveLots={carpark.live_lots} />
        </>
      ) : carpark.tier !== null && carpark.forecast_lots !== null ? (
        <>
          <p className="forecast-card__headline">~{carpark.forecast_lots} lots free in 20 min</p>
          <TierBadge tier={carpark.tier} theme={theme} />
          <LiveCountLine liveLots={carpark.live_lots} />
          <TransparencyNote modelVersion={carpark.model_version} />
        </>
      ) : (
        // Defensive fallback: db/schema.sql's carpark_forecast_shape check
        // constraint guarantees ml/baseline rows carry forecast_lots+tier,
        // but a network payload is never trusted blindly -- degrade to the
        // live count instead of rendering a broken headline.
        <LiveCountLine liveLots={carpark.live_lots} />
      )}
    </div>
  );
}

function LiveCountLine({ liveLots }: { liveLots: number }) {
  return <p className="forecast-card__live">{liveLots} lots available now</p>;
}

function TransparencyNote({ modelVersion }: { modelVersion: string | null }) {
  const text = modelVersion
    ? `Learned from historical patterns (model ${modelVersion})`
    : "Based on recent historical averages";
  return <p className="forecast-card__note">{text}</p>;
}

function ShareButton({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  const handleShare = (): void => {
    void copyToClipboard(url).then((ok) => {
      if (ok) {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      }
    });
  };

  return (
    <button type="button" className="button" onClick={handleShare}>
      {copied ? "Copied!" : "Share"}
    </button>
  );
}

function ForecastCardSkeleton({ name }: { name: string }) {
  return (
    <div className="skeleton-card" role="status" aria-label={`Loading forecast for ${name}`}>
      <div className="skeleton" style={{ width: "60%" }} />
      <div className="skeleton" style={{ width: "40%", height: "2em" }} />
      <div className="skeleton" style={{ width: "50%" }} />
    </div>
  );
}
