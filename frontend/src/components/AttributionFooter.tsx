interface AttributionFooterProps {
  /** True when any carpark in the current payload is in the "ml" state (design doc D13). */
  hasMlModel: boolean;
}

/**
 * Licence attribution (Singapore Open Data Licence v1.0, requirement 7).
 * The base sentence is always shown; once a SINPA-pretrained model is
 * actually serving (state "ml" on any carpark), a second clause names the
 * SINPA historical dataset under the same licence family (design doc D13).
 */
export function AttributionFooter({ hasMlModel }: AttributionFooterProps) {
  const year = new Date().getFullYear();
  return (
    <footer className="attribution-footer">
      <p>
        Contains information from LTA DataMall's Carpark Availability dataset, accessed {year},
        made available under the Singapore Open Data Licence v1.0.
        {hasMlModel
          ? " Also contains information from the SINPA historical carpark dataset, made available under the Singapore Open Data Licence v1.0, used to pretrain the forecasting model."
          : null}
      </p>
    </footer>
  );
}
