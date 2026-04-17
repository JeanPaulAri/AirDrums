using System.Collections;
using UnityEngine;

public class StickVisualizer : MonoBehaviour
{
    [Header("References")]
    [SerializeField] private Transform visualTarget;
    [SerializeField] private Camera projectionCamera;
    [SerializeField] private Transform drumRoot;
    [SerializeField] private Transform stickProjectionAnchor;

    [Header("Stick Shape")]
    [SerializeField] private float shaftLength = 0.55f;
    [SerializeField] private float tipLift = 0.02f;

    [Header("Orientation")]
    [SerializeField] private float upwardWeight = 0.60f;
    [SerializeField] private float towardCameraWeight = 0.75f;
    [SerializeField] private float motionWeight = 0.35f;
    
    [SerializeField] private Vector3 modelForwardAxis = Vector3.right;
    [SerializeField] private Vector3 localRotationOffsetEuler = Vector3.zero;
    [SerializeField] private float cameraFacingBias = 0.35f;
    
    [Header("Smoothing")]
    [SerializeField] private float positionLerpSpeed = 18f;
    [SerializeField] private float rotationLerpSpeed = 14f;

    [Header("Visibility")]
    [SerializeField] private bool hideWhenNoTracking = true;

    [Header("Hit Feedback")]
    [SerializeField] private float pulseScaleMultiplier = 1.15f;
    [SerializeField] private float pulseDuration = 0.08f;

    private bool hasTracking;
    private Vector2 targetViewport;
    private Vector3 currentTipWorld;
    private Vector3 lastTipWorld;
    private bool initialized;
    private Coroutine pulseRoutine;
    private Vector3 initialScale;

    private void Awake()
    {
        if (visualTarget == null && transform.childCount > 0)
        {
            visualTarget = transform.GetChild(0);
        }

        if (projectionCamera == null)
        {
            projectionCamera = Camera.main;
        }

        if (visualTarget != null)
        {
            initialScale = visualTarget.localScale;
        }
    }

    public void SetViewportTrackingPosition(Vector2 viewportPosition)
    {
        targetViewport = viewportPosition;
        hasTracking = true;

        if (hideWhenNoTracking && visualTarget != null && !visualTarget.gameObject.activeSelf)
        {
            visualTarget.gameObject.SetActive(true);
        }
    }

    public void ClearTracking()
    {
        hasTracking = false;
        initialized = false;
        
        if (hideWhenNoTracking && visualTarget != null)
        {
            visualTarget.gameObject.SetActive(false);
        }
    }

    // Compatibilidad por si otro codigo aun llama al metodo viejo
    public void SetNormalizedTrackingPosition(float normalizedX, float normalizedY)
    {
        SetViewportTrackingPosition(new Vector2(normalizedX, 1f - normalizedY));
    }

    public void PulseHitFeedback()
    {
        if (visualTarget == null)
        {
            return;
        }

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

    private void LateUpdate()
    {
        if (projectionCamera == null || drumRoot == null || stickProjectionAnchor == null || visualTarget == null)
        {
            return;
        }

        if (!hasTracking)
        {
            return;
        }

        float projectionDepth = GetProjectionDepth();
        Vector3 targetTipWorld = projectionCamera.ViewportToWorldPoint(
            new Vector3(targetViewport.x, targetViewport.y, projectionDepth)
        );

        targetTipWorld += drumRoot.up * tipLift;

        if (!initialized)
        {
            currentTipWorld = targetTipWorld;
            lastTipWorld = targetTipWorld;
            initialized = true;
        }

        float posT = 1f - Mathf.Exp(-positionLerpSpeed * Time.deltaTime);
        currentTipWorld = Vector3.Lerp(currentTipWorld, targetTipWorld, posT);

        Vector3 velocityWorld = (currentTipWorld - lastTipWorld) / Mathf.Max(Time.deltaTime, 0.0001f);
        lastTipWorld = currentTipWorld;

        Vector3 towardCamera = -projectionCamera.transform.forward;
        Vector3 handleDirection =
            (Vector3.up * upwardWeight) +
            (towardCamera * towardCameraWeight);

        if (velocityWorld.sqrMagnitude > 0.0001f)
        {
            handleDirection += velocityWorld.normalized * motionWeight;
        }

        handleDirection.Normalize();

        // Centro del stick: la punta queda adelante, el cuerpo va "hacia atras"
        Vector3 targetCenterWorld = currentTipWorld + (handleDirection * (shaftLength * 0.5f));
        transform.position = Vector3.Lerp(transform.position, targetCenterWorld, posT);

        // Bias adicional para que el stick "venga" más desde la camara
        Vector3 finalDirection = (handleDirection + (towardCamera * cameraFacingBias)).normalized;

        // Ojo: aqui usamos el eje real del modelo, no asumimos Vector3.up
        Quaternion axisRotation = Quaternion.FromToRotation(modelForwardAxis.normalized, finalDirection);
        Quaternion offsetRotation = Quaternion.Euler(localRotationOffsetEuler);

        Quaternion targetRotation = axisRotation * offsetRotation;

        float rotT = 1f - Mathf.Exp(-rotationLerpSpeed * Time.deltaTime);
        transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rotT);
    }

    private float GetProjectionDepth()
    {
        Vector3 viewport = projectionCamera.WorldToViewportPoint(stickProjectionAnchor.position);
        return Mathf.Max(0.1f, viewport.z);
    }
}