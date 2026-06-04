document.addEventListener('DOMContentLoaded', () => {
  // --- Mobile Navigation Menu Toggle ---
  const menuToggle = document.getElementById('menu-toggle');
  const navLinks = document.getElementById('nav-links');
  
  if (menuToggle && navLinks) {
    menuToggle.addEventListener('click', () => {
      navLinks.classList.toggle('active');
      menuToggle.classList.toggle('active');
    });

    // Close menu when navigation link is clicked
    navLinks.querySelectorAll('a').forEach(link => {
      link.addEventListener('click', () => {
        navLinks.classList.remove('active');
        menuToggle.classList.remove('active');
      });
    });
  }

  // --- Terminal Tabs (CLI Demo Sandbox) ---
  const terminalTabs = document.querySelectorAll('.terminal-tab');
  const terminalContents = document.querySelectorAll('.terminal-content');

  terminalTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      // Remove active class from all tabs
      terminalTabs.forEach(t => t.classList.remove('active'));
      // Add active class to clicked tab
      tab.classList.add('active');

      // Get target tab content ID
      const targetId = tab.getAttribute('data-tab');

      // Hide all contents and show target content
      terminalContents.forEach(content => {
        if (content.id === targetId) {
          content.classList.add('active');
        } else {
          content.classList.remove('active');
        }
      });
    });
  });

  // --- Installation Tabs ---
  const installTabs = document.querySelectorAll('.install-tab-btn');
  const installContents = document.querySelectorAll('.install-tab-content');

  installTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      installTabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      const targetId = tab.getAttribute('data-install');

      installContents.forEach(content => {
        if (content.id === targetId) {
          content.classList.add('active');
        } else {
          content.classList.remove('active');
        }
      });
    });
  });

  // --- Copy-to-Clipboard Functionality ---
  const copyButtons = document.querySelectorAll('.copy-btn');

  copyButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-target');
      const textToCopy = document.getElementById(targetId)?.textContent;

      if (textToCopy) {
        navigator.clipboard.writeText(textToCopy).then(() => {
          // Provide visual feedback
          const originalIcon = btn.innerHTML;
          
          // Render checkmark icon
          btn.innerHTML = `
            <svg fill="none" stroke="hsl(145, 65%, 55%)" stroke-width="2" viewBox="0 0 24 24" style="width: 18px; height: 18px;">
              <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          `;
          btn.style.borderColor = 'hsl(145, 65%, 55%)';
          
          setTimeout(() => {
            btn.innerHTML = originalIcon;
            btn.style.borderColor = '';
          }, 2000);
        }).catch(err => {
          console.error('Failed to copy text: ', err);
        });
      }
    });
  });

  // --- Scroll-driven Navigation Highlights ---
  const sections = document.querySelectorAll('section');
  const navItems = document.querySelectorAll('.nav-links a:not(.nav-btn)');

  window.addEventListener('scroll', () => {
    let currentSectionId = '';

    sections.forEach(section => {
      const sectionTop = section.offsetTop;
      const sectionHeight = section.clientHeight;
      if (window.scrollY >= (sectionTop - 150)) {
        currentSectionId = section.getAttribute('id');
      }
    });

    navItems.forEach(item => {
      item.classList.remove('active');
      if (item.getAttribute('href') === `#${currentSectionId}`) {
        item.style.color = 'var(--accent-cyan)';
      } else {
        item.style.color = '';
      }
    });
  });

  // --- Scroll-reveal fallback for unsupported browsers ---
  if (!CSS.supports('(animation-timeline: view()) and (animation-range: entry)')) {
    const revealObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('revealed');
          revealObserver.unobserve(entry.target); // Stop observing once revealed
        }
      });
    }, {
      threshold: 0.1,
      rootMargin: '0px 0px -50px 0px'
    });

    document.querySelectorAll('.reveal-on-scroll').forEach(el => {
      revealObserver.observe(el);
    });
  }
});
