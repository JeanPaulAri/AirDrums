using System.Collections;
using UnityEngine;

public class StickVisualizer : MonoBehaviour
{
    [Header("Visual Target")]
    [SerializeField] private Transform visualTarget;

    [Header("Tracking Range (Local Space)")]
    [SerializeField] private Vector3 minLocalPosition = new Vector3(-0.8f, 0.8f, 0.2f);
    [SerializeField] private Vector3 maxLocalPosition = new Vector3(0.8f, 1.6f, 1.6f);

    [Header("Smoothing")]
    [SerializeField] private float smoothTime = 0.05f;

    [Header("Hit Feedback")]
    [SerializeField] private float pulseScaleMultiplier = 1.15f;
    [SerializeField] private float pulseDuration = 0.08f;

    private Vector3 currentVelocity;
    private Vector3 targetLocalPosition;
    private Vector3 initialScale;
    private Coroutine pulseRoutine;

    private void Awake()
    {
        if (visualTarget == null)
        {
            visualTarget = transform;
        }

        targetLocalPosition = visualTarget.localPosition;
        initialScale = visualTarget.localScale;
    }

    private void Update()
    {
        visualTarget.localPosition = Vector3.SmoothDamp(
            visualTarget.localPosition,
            targetLocalPosition,
            ref currentVelocity,
            smoothTime
        );
    }

    public void SetNormalizedTrackingPosition(float normalizedX, float normalizedY)
    {
        normalizedX = Mathf.Clamp01(normalizedX);
        normalizedY = Mathf.Clamp01(normalizedY);

        float x = Mathf.Lerp(minLocalPosition.x, maxLocalPosition.x, normalizedX);
        float y = Mathf.Lerp(maxLocalPosition.y, minLocalPosition.y, normalizedY);
        float z = Mathf.Lerp(minLocalPosition.z, maxLocalPosition.z, normalizedY);

        targetLocalPosition = new Vector3(x, y, z);
    }

    public void PulseHitFeedback()
    {
        if (pulseRoutine != null)
        {
            StopCoroutine(pulseRoutine);
        }

        pulseRoutine = StartCoroutine(PulseRoutine());
    }

    private IEnumerator PulseRoutine()
    {
        visualTarget.localScale = initialScale * pulseScaleMultiplier;
        yield return new WaitForSeconds(pulseDuration);
        visualTarget.localScale = initialScale;
        pulseRoutine = null;
    }
}
