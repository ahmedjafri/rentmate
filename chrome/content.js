// --- DOM SELECTORS ---
// (No changes needed here)
const chatContainerSelector = 'div[class="messenger-layout-message-details"]';
const messageBodySelector = 'div[data-test="message-body"]';
const messageInputSelector = '[data-test="message-input"] textarea';
const senderNameSelector = 'p[data-test="sender-name"]';
const senderContainerSelector = 'div[class="d-flex align-items-end"]';
const maintenanceHeaderSelector = 'messenger-maintenance-connected-info';
const maintenanceTitleSelector = 'b[data-test="maintenance-title"]';
const maintenanceDescriptionSelector = 'p[data-test="maintenance-description"]';

/**
 * Creates and injects a loading UI while the suggestion is being generated.
 */
function injectLoadingUI() {
    const existingUi = document.getElementById('agent-suggestion-box');
    if (existingUi) {
        existingUi.remove();
    }

    const chatContainer = document.querySelector(chatContainerSelector);
    if (!chatContainer) return;

    const loadingBox = document.createElement('div');
    loadingBox.id = 'agent-suggestion-box';
    loadingBox.style.cssText = `
        position: absolute;
        bottom: 70px;
        left: 50%;
        transform: translateX(-50%);
        width: 90%;
        max-width: 500px;
        background-color: #fff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        padding: 10px;
        text-align: center;
        z-index: 1000;
        transition: opacity 0.3s ease;
    `;
    loadingBox.textContent = 'Generating suggestion...';

    chatContainer.style.position = 'relative';
    chatContainer.appendChild(loadingBox);
}

/**
 * Creates and injects the agent suggestion UI into the chat.
 * @param {string} suggestionText The text to display in the suggestion box.
 */
function injectSuggestionUI(suggestionText) {
    console.log('Injecting suggestion UI...');
    // Check if the suggestion UI is already there to prevent duplicates
    const existingUi = document.getElementById('agent-suggestion-box');
    if (existingUi) {
        existingUi.remove();
    }

    const chatContainer = document.querySelector(chatContainerSelector);
    if (!chatContainer) {
        console.error('Chat container not found during UI injection. This is unexpected.');
        return;
    }

    const suggestionBox = document.createElement('div');
    suggestionBox.id = 'agent-suggestion-box';
    suggestionBox.style.cssText = `
        position: absolute;
        bottom: 70px; /* Adjust this value to position it correctly above the input */
        left: 50%;
        transform: translateX(-50%);
        width: 90%;
        max-width: 500px;
        background-color: #fff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        z-index: 1000;
        transition: opacity 0.3s ease;
    `;

    const suggestionTextElement = document.createElement('p');
    suggestionTextElement.style.cssText = `
        margin: 0;
        font-size: 14px;
        color: #4a5568;
        font-style: italic;
    `;
    suggestionTextElement.textContent = suggestionText;

    const buttonContainer = document.createElement('div');
    buttonContainer.style.cssText = `
        display: flex;
        justify-content: flex-end;
        gap: 10px;
    `;

    const useButton = document.createElement('button');
    useButton.style.cssText = `
        background-color: #4c51bf;
        color: #fff;
        border: none;
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 14px;
        cursor: pointer;
        transition: background-color 0.2s;
    `;
    useButton.textContent = 'Use this suggestion';

    const rejectButton = document.createElement('button');
    rejectButton.style.cssText = `
        background-color: #e2e8f0;
        color: #4a5568;
        border: none;
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 14px;
        cursor: pointer;
        transition: background-color 0.2s;
    `;
    rejectButton.textContent = 'Reject';

    // Click handler for the use button
    useButton.addEventListener('click', () => {
        const messageInput = document.querySelector(messageInputSelector);
        if (messageInput) {
            messageInput.value = suggestionText; // Set the input value
            // Dispatching an 'input' event can help trigger any listeners the original app has
            messageInput.dispatchEvent(new Event('input', { bubbles: true }));
            suggestionBox.remove(); // Remove the suggestion UI after use
        } else {
            console.error('Message input not found.');
        }
    });

    // Click handler for the reject button
    rejectButton.addEventListener('click', () => {
        suggestionBox.remove(); // Simply remove the UI
    });

    suggestionBox.appendChild(suggestionTextElement);
    buttonContainer.appendChild(rejectButton);
    buttonContainer.appendChild(useButton);
    suggestionBox.appendChild(buttonContainer);
    chatContainer.style.position = 'relative'; // Ensure relative positioning for the absolute UI
    chatContainer.appendChild(suggestionBox);
}

/**
 * Retrieves the text of the entire conversation history.
 * @returns {Array<Object>} The full conversation history.
 */
function getConversationHistory() {
    const messages = [];
    const messageElements = document.querySelectorAll('messenger-list-item');
    messageElements.forEach(messageElement => {
        const senderNameElement = messageElement.querySelector(senderNameSelector);
        const messageBodyElement = messageElement.querySelector(messageBodySelector);
        if (senderNameElement && messageBodyElement) {
            const sender = senderNameElement.textContent.trim();
            const text = messageBodyElement.textContent.trim();
            messages.push({ sender: sender, text: text });
        }
    });
    return messages;
}

/**
 * Adds the "Suggest" button to the chat UI.
 * This function is now idempotent, meaning it can be called multiple times
 * without creating duplicates.
 */
function addSuggestionButton() {
    const senderContainer = document.querySelector(senderContainerSelector);
    if (!senderContainer) {
        // If the container is not found, we simply stop.
        return;
    }

    // Check if the button already exists to prevent duplicates.
    // We check for the element's presence in the DOM, not just its ID.
    if (document.getElementById('suggest-button')) {
        return;
    }
    
    // Create and style the button.
    const suggestButton = document.createElement('button');
    suggestButton.id = 'suggest-button';
    suggestButton.textContent = 'Suggest';
    suggestButton.title = 'Get a quick suggestion from the agent';
    suggestButton.style.cssText = `
        background-color: #4c51bf;
        color: #fff;
        border: none;
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 14px;
        cursor: pointer;
        transition: background-color 0.2s;
        margin-right: 8px;
    `;

    suggestButton.addEventListener('click', async () => {
        let headerTitle = '';
        let headerDescription = '';
        const headerElement = document.querySelector(maintenanceHeaderSelector);
        if (headerElement) {
            const titleElement = headerElement.querySelector(maintenanceTitleSelector);
            if (titleElement) {
                headerTitle = titleElement.textContent.trim();
            }
            const descriptionElement = headerElement.querySelector(maintenanceDescriptionSelector);
            if (descriptionElement) {
                headerDescription = descriptionElement.textContent.trim();
            }
        }

        const conversationHistory = getConversationHistory();
        if (conversationHistory.length > 0) {
            injectLoadingUI();
            try {
                const suggestion = await getAgentSuggestion(conversationHistory, headerTitle, headerDescription);
                injectSuggestionUI(suggestion);
            } catch (error) {
                console.error('Error getting agent suggestion:', error);
                const existingUi = document.getElementById('agent-suggestion-box');
                if (existingUi) existingUi.remove();
            }
        } else {
            console.log('No messages found in the conversation to suggest a response for.');
        }
    });

    const messageInput = document.querySelector(messageInputSelector);
    if (messageInput) {
        const parentElement = messageInput.closest('messenger-sender-message');
        if (parentElement) {
            // Find the correct insertion point.
            // We use the `parentElement` to ensure we insert the button in the correct container.
            parentElement.insertBefore(suggestButton, parentElement.firstChild);
        } else {
            console.error('Parent element for message input not found.');
        }
    } else {
        console.error('Message input not found, cannot place suggestion button.');
    }
}

/**
 * Initializes observers to watch for necessary elements to appear.
 * This is the corrected and more robust implementation.
 */
function initializeObservers() {
    console.log('Initializing observer for sender container...');

    // We now observe the entire body for changes.
    const bodyObserver = new MutationObserver((mutationsList, observer) => {
        // We can optimize by checking if the added node is a potential parent of our target.
        const isSenderContainerAdded = mutationsList.some(mutation =>
            mutation.addedNodes.length > 0 &&
            document.querySelector(senderContainerSelector)
        );

        if (isSenderContainerAdded) {
            // Check if the button is already there to prevent re-adding it.
            if (!document.getElementById('suggest-button')) {
                addSuggestionButton();
            }
        }
    });

    // Start observing the body for child list and subtree changes.
    bodyObserver.observe(document.body, { childList: true, subtree: true });

    // Initial check in case the element is already there when the script loads.
    addSuggestionButton();
}

// Start the observers once the page is fully loaded.
window.addEventListener('load', initializeObservers);