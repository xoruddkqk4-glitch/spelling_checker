(function() {
  function updateModelLabel() {
    const settingsButton = document.getElementById("chat-settings-open-modal");
    if (!settingsButton) return;

    fetch("/public/current_model.json")
      .then(response => {
        if (!response.ok) throw new Error("File not found");
        return response.json();
      })
      .then(data => {
        const modelId = data.model_name || "google/gemma-4-12b";
        let displayName = "Gemma 4 12B";
        if (modelId === "google/gemma-4-26b-a4b") {
          displayName = "Gemma 4 26B A4B";
        } else if (modelId === "google/gemma-4-e4b") {
          displayName = "Gemma 4 E4B";
        }

        let label = document.getElementById("model-display-label");
        if (!label) {
          label = document.createElement("span");
          label.id = "model-display-label";
          // Apply premium style compatible with light/dark modes
          label.style.fontSize = "11px";
          label.style.fontWeight = "600";
          label.style.color = "#a78bfa"; // Sleek light purple/indigo text
          label.style.backgroundColor = "rgba(139, 92, 246, 0.15)";
          label.style.border = "1px solid rgba(139, 92, 246, 0.3)";
          label.style.borderRadius = "20px";
          label.style.padding = "2px 10px";
          label.style.marginRight = "6px";
          label.style.display = "inline-flex";
          label.style.alignItems = "center";
          label.style.height = "24px";
          label.style.fontFamily = "'Inter', 'Outfit', sans-serif";
          label.style.whiteSpace = "nowrap";
          
          // Insert label before the settings button
          settingsButton.parentNode.insertBefore(label, settingsButton);
        }

        if (label.textContent !== displayName) {
          label.textContent = displayName;
        }
      })
      .catch(err => {
        // Quietly fail or do nothing if the JSON file is not ready yet
      });
  }

  // Poll every 1 second
  setInterval(updateModelLabel, 1000);
})();
